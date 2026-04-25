# Implementation Spec: Full Performance Overhaul

**Target Hardware:** Apple MacBook Pro M4 Max, 128GB Unified Memory  
**Memory Bandwidth:** ~546 GB/s  
**Date:** 2025-04-25  
**Version:** 0.3.0  

---

## Executive Summary

This spec covers a full performance overhaul of MLX Task Router, optimized for M4 Max 128GB. Five phases target: optimal model selection, I/O performance, inference speed, smarter routing, and observability. Each phase is independently deployable and tested.

---

## Phase 1: Model Selection & Hardware Tuning

### 1.1 Model Selection

**Current:** `Qwen3-Coder-30B-A3B-Instruct-4bit` (MoE, ~3B active params, ~17GB)  
- Fast inference (~60+ tok/s on M4 Max due to small active params)
- Weaker tool-calling accuracy (only 3B params reason per token)
- Higher fallback rate to cloud

**Recommended:** `Qwen2.5-Coder-32B-Instruct-4bit` (~19GB)  
- Dense 32B model, all parameters active — significantly better tool-calling quality
- Purpose-built for code tasks with function-calling training
- ~25-35 tok/s on M4 Max at 4-bit (adequate for CLI tasks — most responses are <200 tokens)
- 19GB leaves ~109GB free for OS, Claude Code, caching, and other apps
- Best quality-to-speed ratio for the router's CLI-task purpose

**Alternative for max quality:** `Qwen2.5-Coder-32B-Instruct-8bit` (~38GB)  
- Higher quality from less quantization loss
- ~15-20 tok/s — slower but still acceptable for short CLI responses
- 38GB still leaves ~90GB free on 128GB machine

**Decision criteria:** Run the benchmarking harness (Phase 5) with both models and compare tool-calling accuracy vs. speed. Start with 4-bit.

### 1.2 Model Warmup

**Problem:** First request after startup is slow — MLX lazy-loads weights and compiles Metal kernels on first forward pass.

**Solution:** After `load_model()`, run a short warmup generation (10 tokens with a minimal prompt) to prime Metal shader caches and memory mapping.

**File:** `src/mlx_task_router/local.py`  
**Method:** Add `warmup()` to `ModelManager`, called from `load_model()`.

```python
def warmup(self) -> None:
    """Prime Metal shader cache and memory mapping with a short generation."""
    if not self.is_loaded:
        return
    t0 = time.time()
    prompt = self._apply_chat_template(
        [{"role": "user", "content": "hi"}], None
    )
    from mlx_lm import generate as mlx_generate
    mlx_generate(self._model, self._tokenizer, prompt=prompt, max_tokens=1, verbose=False)
    print(f"[model] Warmup complete in {time.time() - t0:.1f}s")
```

### 1.3 Generation Parameters

**Current:** Only `max_tokens` is configured. Temperature, top_p, repetition_penalty are all MLX defaults.

**For CLI tool-calling tasks, optimal params:**
- `temperature=0.0` — deterministic tool calls, no creativity needed
- `repetition_penalty=1.05` — prevent degenerate loops on malformed outputs
- `top_p=1.0` — no nucleus sampling at temp=0

**File:** `src/mlx_task_router/local.py` — pass to `mlx_generate()` and `mlx_stream()`.

**Config:** Add `MLX_TEMPERATURE` env var (default `0.0`) to `config.py`.

---

## Phase 2: Connection & I/O Performance

### 2.1 Persistent HTTP Client (Connection Pooling)

**Problem:** `proxy.py` creates a new `httpx.AsyncClient` per request (lines 60, 78). Each request pays TCP handshake + TLS negotiation to `api.anthropic.com` (~50-100ms).

**Solution:** Module-level persistent `httpx.AsyncClient` with connection pooling. Reuses TCP connections across requests.

**File:** `src/mlx_task_router/proxy.py`

```python
# Module-level persistent client — reuses connections
_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30,
            ),
        )
    return _client

async def shutdown_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
```

Update `forward_request` and `stream_forward` to use `_get_client()` instead of `async with httpx.AsyncClient(...)`.

Add `shutdown_client()` call to server lifespan shutdown.

**Impact:** Eliminates ~50-100ms per forwarded request. Significant when multiple requests are forwarded in sequence.

### 2.2 Real-Time Token Streaming

**Problem:** `stream_generate()` in `local.py` buffers the ENTIRE response before emitting any SSE events (lines 194-207). The function name says "stream" but the user sees nothing until generation is complete. TTFT (time-to-first-token) = full generation time.

**Current flow:**
```
[user request] → [buffer ALL tokens] → [parse tool calls] → [emit SSE events]
```

**New flow (for text-only responses):**
```
[user request] → [stream tokens live as SSE] → [if tool_call detected, buffer remainder]
```

**Strategy:** Stream text tokens in real-time. If a `<tool_call>` tag is detected mid-stream, switch to buffering mode to parse the complete tool call JSON. This gives instant TTFT for text responses while maintaining correct tool-call parsing.

**File:** `src/mlx_task_router/local.py` — rewrite `stream_generate()`.

**Key implementation details:**
- Maintain a small lookahead buffer (~50 chars) to detect `<tool_call>` opening
- If no tool call detected after generation completes, all text was streamed live
- If tool call detected, buffer from that point, parse, and emit tool_use blocks
- Track token count incrementally during streaming

**Complexity:** Medium-high. Requires careful state management for the hybrid stream/buffer approach. Must maintain correct SSE event ordering.

### 2.3 Request Body Caching

**Problem:** `await request.json()` is called, then `MessagesRequest(**body)` parses it. If parse fails, the raw body is forwarded. But `request.json()` is already decoded — we re-encode it for forwarding.

**Solution:** Use `await request.body()` to get raw bytes. Parse JSON once. Forward raw bytes when needed (avoiding re-serialization).

**File:** `src/mlx_task_router/server.py`  
**Impact:** Minor — saves JSON re-serialization on forwarded requests.

---

## Phase 3: Inference Optimization

### 3.1 Speculative Decoding

**Concept:** Use a small "draft" model (e.g., Qwen2.5-Coder-1.5B-Instruct-4bit, ~1GB) to generate candidate tokens, then verify them in batch with the main model. Tokens that match are "free" — you get the quality of the large model at closer to the speed of the small model.

**MLX support:** `mlx-lm` supports speculative decoding via `mlx_lm.generate()` with a `draft_model` parameter.

**Configuration:**
```
MLX_DRAFT_MODEL=mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit
MLX_SPECULATIVE_TOKENS=5  # candidates per step
```

**File:** `src/mlx_task_router/local.py`
- Load draft model alongside main model in `load_model()`
- Pass `draft_model` to `mlx_generate()` when available
- Draft model memory: ~1GB — negligible on 128GB system

**Expected speedup:** 1.5-2.5x for CLI-style responses where the draft model can predict common tool-calling patterns.

**Risk:** Draft model loading adds ~2-3s to startup. Draft model must use same tokenizer family. Speculative decoding has overhead per step — net negative for very short responses (<10 tokens). Gate behind config flag.

### 3.2 Prompt Caching (System Prompt KV Reuse)

**Concept:** The local system prompt (`LOCAL_SYSTEM_PROMPT`) is prepended to every local request. Encoding and processing it through the model's attention layers every time is wasteful. Cache the KV state for the system prompt and reuse it.

**MLX support:** `mlx-lm` >= 0.26 supports prompt caching via `cache_history` parameter.

**Implementation:**
- After warmup, generate KV cache for `LOCAL_SYSTEM_PROMPT` + chat template prefix
- Store cache object on `ModelManager`
- Pass cached KV to subsequent `generate()` calls
- Invalidate cache if model changes

**File:** `src/mlx_task_router/local.py`

**Expected speedup:** Saves processing of ~150-200 tokens of system prompt per request. More impactful as the system prompt grows.

**Dependency:** Need to verify `mlx-lm` API for cache passing. This is a stretch goal — implement only if the API supports it cleanly.

### 3.3 Generation Config

Add configurable generation parameters to `config.py`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MLX_TEMPERATURE` | `0.0` | Sampling temperature (0 = greedy) |
| `MLX_TOP_P` | `1.0` | Nucleus sampling threshold |
| `MLX_REPETITION_PENALTY` | `1.05` | Prevent degenerate repetition |
| `MLX_DRAFT_MODEL` | `""` | Draft model for speculative decoding (empty = disabled) |
| `MLX_SPECULATIVE_TOKENS` | `5` | Candidate tokens per speculative step |

---

## Phase 4: Smarter Routing

### 4.1 Adaptive Routing Threshold

**Problem:** `ROUTING_THRESHOLD` is a fixed 0.5. The optimal threshold depends on model quality, user workflow, and task distribution. A strong 32B model can handle more tasks locally than a 3B-active MoE.

**Solution:** Auto-calibrate threshold from feedback data. When the local routing success rate is high (>90%), lower the threshold slightly to capture more tasks. When failure rate increases, raise it.

**Algorithm:**
```python
def adaptive_threshold() -> float:
    base = config.routing_threshold  # 0.5 default
    fb = routing_feedback.stats()
    
    total_attempts = sum(t["attempts"] for t in fb.values())
    total_failures = sum(t["failures"] for t in fb.values())
    
    if total_attempts < 20:
        return base  # not enough data
    
    failure_rate = total_failures / total_attempts
    
    # Shift threshold: low failures → lower threshold (more local)
    # High failures → higher threshold (more conservative)
    adjustment = (failure_rate - 0.1) * 0.5  # centered at 10% failure rate
    return max(0.2, min(0.8, base + adjustment))
```

**File:** `src/mlx_task_router/router.py`  
**Config:** `ADAPTIVE_ROUTING=true` env var (default `true`).

### 4.2 Accurate Token Estimation

**Problem:** `_estimate_tokens()` uses `char_count // 4` — a rough heuristic. Overestimates for code (variable names are long), underestimates for languages with short tokens.

**Solution:** When the model is loaded, use the actual tokenizer for context estimation. Fall back to heuristic when model unavailable.

**File:** `src/mlx_task_router/router.py`

```python
def _estimate_tokens(messages, system=None) -> int:
    if model_manager.is_loaded:
        # Use actual tokenizer — accurate count
        text = _concatenate_message_text(messages, system)
        return model_manager._count_tokens(text)
    # Fallback: heuristic
    return _estimate_tokens_heuristic(messages, system)
```

**Impact:** More accurate routing decisions. Prevents unnecessary forwarding of requests that fit in local context, and prevents sending too-large contexts locally.

### 4.3 Expanded CLI Action Phrases

Add more patterns for common developer workflows:

```python
# Additional patterns
r"\b(cherry[- ]?pick|rebase|merge)\s+(the\s+)?(branch|commit|pr)\b",
r"\b(lint|format|type[- ]?check)\s+(the\s+)?(code|files?|project)\b",
r"\b(deploy|publish|release)\s+(to\s+)?(staging|prod|npm|pypi)\b",
r"\b(start|stop|restart)\s+(the\s+)?(server|service|container|docker)\b",
r"\b(tail|grep|cat|head)\s+(the\s+)?(logs?|file|output)\b",
r"\bkill\s+(the\s+)?(process|server|port)\b",
```

**File:** `src/mlx_task_router/router.py`

---

## Phase 5: Observability & Benchmarking

### 5.1 Request Timing Middleware

Add FastAPI middleware that records per-request timing:
- Total request duration
- Routing decision time
- Generation time (local) or upstream latency (forwarded)
- Tokens per second (local)

**File:** `src/mlx_task_router/perf.py` (new)

```python
@dataclass
class RequestMetrics:
    timestamp: float
    route: str  # "local" | "forward" | "cache"
    total_ms: float
    routing_ms: float
    generation_ms: float  # or upstream_ms
    tokens_per_sec: float  # local only
    input_tokens: int
    output_tokens: int
```

Store last N metrics in a ring buffer. Expose via `/perf` endpoint.

### 5.2 Performance Dashboard Endpoint

**Endpoint:** `GET /perf`

Returns:
```json
{
    "latency_p50_ms": 145,
    "latency_p95_ms": 890,
    "latency_p99_ms": 1250,
    "local_tokens_per_sec": 28.5,
    "local_avg_generation_ms": 340,
    "forward_avg_latency_ms": 1200,
    "routing_avg_ms": 0.8,
    "requests_last_hour": 47,
    "cache_hit_rate": "34%"
}
```

### 5.3 Benchmarking Harness

**File:** `tests/benchmark.py` (new)

A standalone script that:
1. Sends N representative requests (mix of CLI and complex tasks)
2. Measures tokens/sec, TTFT, total latency, routing accuracy
3. Compares against a ground-truth routing file (manually labeled)
4. Outputs a report:

```
Model: Qwen2.5-Coder-32B-Instruct-4bit
Requests: 50 (30 CLI, 20 complex)
Routing accuracy: 94% (47/50)
Local tok/s (p50): 28.3
Local TTFT (p50): 89ms
Local total (p50): 340ms
Forward total (p50): 1240ms
Fallback rate: 4% (2/50)
```

**Benchmark dataset:** `tests/fixtures/benchmark_requests.json` — 50 representative requests with expected routing labels.

---

## Implementation Order

| Step | Phase | Description | Files Changed | Est. Effort |
|------|-------|-------------|---------------|-------------|
| 1 | 1.1 | Switch default model to Qwen2.5-Coder-32B | `config.py` | 5 min |
| 2 | 1.2 | Add model warmup | `local.py` | 15 min |
| 3 | 1.3 | Generation parameters (temp, rep_penalty) | `config.py`, `local.py` | 15 min |
| 4 | 2.1 | Persistent HTTP client | `proxy.py`, `server.py` | 20 min |
| 5 | 2.2 | Real-time token streaming | `local.py` | 45 min |
| 6 | 4.1 | Adaptive routing threshold | `router.py`, `config.py` | 20 min |
| 7 | 4.2 | Accurate token estimation | `router.py` | 15 min |
| 8 | 4.3 | Expanded CLI action phrases | `router.py` | 10 min |
| 9 | 5.1 | Request timing middleware | `perf.py` (new), `server.py` | 30 min |
| 10 | 5.2 | Performance dashboard endpoint | `server.py`, `perf.py` | 15 min |
| 11 | 5.3 | Benchmarking harness + fixtures | `tests/benchmark.py` (new) | 30 min |
| 12 | 3.1 | Speculative decoding | `config.py`, `local.py` | 30 min |
| 13 | 3.2 | Prompt KV cache | `local.py` | 30 min |
| 14 | — | Tests + CHANGELOG + README | `tests/`, docs | 30 min |

**Total estimated: ~5 hours**

---

## Testing Strategy

- **Unit tests:** Each phase gets targeted tests in `tests/`
- **Integration test:** Full request round-trip with mocked MLX model
- **Benchmark test:** `tests/benchmark.py` validates routing accuracy
- **Regression:** Run existing 105 tests after each phase
- **Manual verification:** Start server, send requests via curl, verify streaming behavior

---

## Rollback Plan

Each phase is a separate logical commit. If any phase causes issues:
1. Revert the specific commit
2. Config flags (`MLX_DRAFT_MODEL`, `ADAPTIVE_ROUTING`) allow disabling features without code changes
3. Model selection is purely config — switch back to MoE model via `MLX_MODEL` env var

---

## Config Summary (New Environment Variables)

| Variable | Default | Phase | Description |
|----------|---------|-------|-------------|
| `MLX_MODEL` | `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | 1.1 | Primary model (changed default) |
| `MLX_TEMPERATURE` | `0.0` | 1.3 | Generation temperature |
| `MLX_TOP_P` | `1.0` | 1.3 | Nucleus sampling |
| `MLX_REPETITION_PENALTY` | `1.05` | 1.3 | Repetition penalty |
| `MLX_DRAFT_MODEL` | `""` | 3.1 | Draft model for speculative decoding |
| `MLX_SPECULATIVE_TOKENS` | `5` | 3.1 | Speculative candidates per step |
| `ADAPTIVE_ROUTING` | `true` | 4.1 | Auto-calibrate routing threshold |
