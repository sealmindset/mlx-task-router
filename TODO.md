# MLX Task Router — TODO

## Completed

- [x] Fallback to cloud on local failure — if MLX model errors, automatically retry via Anthropic API
- [x] Always count tokens locally — use local model tokenizer instead of forwarding to Anthropic API
- [x] Health check watchdog — pings model every 30s, marks unhealthy after 3 failures, auto-recovers by reloading model
- [x] Confidence scoring — score routing confidence instead of binary local/forward; only route locally above a threshold
- [x] Response quality feedback loop — tracks trigger success/failure rates, applies score penalty to unreliable triggers
- [x] Cache common responses — identical requests return cached results instantly (60s TTL, configurable)
- [x] Model warmup — prime Metal shader caches on startup with a 1-token generation
- [x] Persistent HTTP client — connection pooling for upstream Anthropic requests
- [x] Generation parameters — configurable temperature, top_p, repetition_penalty
- [x] Speculative decoding — optional draft model for faster generation
- [x] Adaptive routing threshold — auto-calibrates based on feedback data
- [x] Accurate token estimation — uses real tokenizer when model is loaded
- [x] Expanded CLI action phrases — 15 pattern groups covering common dev workflows
- [x] Performance metrics — ring buffer of per-request timing, tokens/sec, latency percentiles
- [x] `/perf` endpoint — real-time performance dashboard
- [x] Routing benchmark — 50 labeled test fixtures with accuracy assertions

## High Priority

- [x] Server-level tests (FastAPI TestClient) — 23 tests covering HTTP contract, routing, streaming, fallback, cache, stats, CORS, overrides.
- [x] Fix feedback I/O — batch writes with dirty flag + 30s periodic flush thread. Eliminates disk I/O on every request.
- [x] Add CORS middleware — `allow_origins=["*"]`, all methods, all headers. Required for browser-based AI apps.
- [x] Update pricing table — Claude 4, 3.5, 3 families. `_detect_tier` matches modern model strings (e.g., `claude-sonnet-4-20250514`, `claude-3-5-haiku-20241022`).

## Medium Priority

- [x] Real-time token streaming — text tokens streamed live as they arrive from MLX. Tool calls buffered and emitted as complete blocks.
- [x] Routing decision history endpoint — `GET /routing/history` ring buffer (100 entries), `GET /routing/summary`, `POST /routing/clear`.
- [x] Integration tests with real model — 7 tests gated behind `MLX_AVAILABLE` + `SKIP_INTEGRATION=0`. Tests generation, streaming, tool calling, token counting.
- [x] Config reload endpoint — `POST /config/reload` re-reads `.env`, returns changed fields, auto-reloads model if `MLX_MODEL` changed. `GET /config` for current settings.
- [x] Prompt KV cache — system prompt template cached by hash. Avoids re-encoding identical system prompts across requests.
- [x] Semantic response cache — Jaccard n-gram similarity matching (threshold=0.85). Falls back to exact-match cache. `GET /semantic-cache`, `POST /semantic-cache/clear`.
- [x] Self-annealing routing weights — background thread analyzes feedback every 5min, adjusts signal weights via gradient-free optimization. `GET /annealing`, `POST /annealing/reset`.
- [ ] Per-session stats — track routing patterns per Claude Code session to identify optimization opportunities.

## Low Priority

- [ ] Routing dashboard — web UI at `/dashboard` showing live stats, routing decisions, cost savings.
- [ ] Multi-model routing — trivial→7B, moderate→32B, complex→Claude. Re-enable gear system with new routing philosophy.
- [ ] OpenAI API compatibility layer — `/v1/chat/completions` endpoint. Lets OpenAI-compatible apps also benefit from local routing.
- [ ] Anomaly webhooks — alert when failure rate spikes (n8n integration).

## Neural Engine / Hardware Acceleration

**Current state:** All inference runs on M4 Max GPU (40-core) via MLX/Metal. The 16-core Neural Engine (ANE) sits idle. This is correct — the ANE is architecturally unsuited for LLM inference (32MB SRAM cache cliff, 2048-token context limit, max ~8B params, no public API, CoreML 2-4x overhead). GPU is 2-5x faster for token generation.

- [ ] ANE routing classifier — train a small (~1B or distilled) CoreML classification model to run routing decisions on the Neural Engine. This uses otherwise-idle hardware, consumes zero GPU resources, and could replace regex-based pattern matching with learned semantic routing. Requires CoreML integration and a labeled training dataset from production routing logs.
- [ ] M5 GPU Neural Accelerator readiness — Apple M5 introduces dedicated matrix-multiply units inside the GPU (different from ANE) that deliver 3.3-4x faster TTFT via MLX + Metal 4. Ensure we stay on latest MLX to get automatic support when upgrading to M5 (requires macOS 26.2+).
- [ ] Dual-model ANE+GPU pipeline — run a small auxiliary model (summarizer, intent classifier) on ANE via CoreML concurrently with main 32B model on GPU. Zero contention since they use separate hardware.
