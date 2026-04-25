# MLX Task Router — Integration Guide

## Overview

The MLX Task Router is a **transparent inline HTTP proxy** that sits between any application making Anthropic API calls and the actual Anthropic API. It intercepts messages, scores them for complexity, and routes simple tasks to a local MLX model (free) while forwarding complex tasks to Claude's API (paid).

```
┌─────────────────┐       ┌─────────────────────────────┐
│                  │       │   MLX Task Router (:8888)    │
│  Your AI App     │──HTTP──►                             │
│  (any language)  │       │  1. Parse Anthropic request  │
│                  │       │  2. Score forward signals     │
│                  │       │  3. Route decision            │
│                  │◄─HTTP──│                             │
└─────────────────┘       │  ┌──────────┐ ┌───────────┐  │
                          │  │  LOCAL    │ │  FORWARD   │  │
                          │  │  Qwen3   │ │  Claude    │  │
                          │  │  32B     │ │  API       │  │
                          │  │  (free)  │ │  (paid)    │  │
                          │  └──────────┘ └─────┬──────┘  │
                          └─────────────────────┼─────────┘
                                                │
                                                ▼
                                       api.anthropic.com
```

## How It Works

1. Your app sends a standard Anthropic Messages API request to the router
2. The router **parses** the incoming request
3. It **scores** forward signals (complexity, code generation, extended conversation, etc.)
4. If `forward_score < 0.5` → **routes locally** to Qwen3-32B via MLX (free, ~25-35 tok/s)
5. If `forward_score >= 0.5` or hard guards trigger → **forwards to Claude API** (paid)
6. The response is returned in **the exact same Anthropic API format** — your app can't tell the difference

## Connecting Your AI App

### One-Line Configuration Change

**Before (direct to Claude):**

```env
ANTHROPIC_BASE_URL=https://api.anthropic.com
```

**After (through router):**

```env
ANTHROPIC_BASE_URL=http://localhost:8888
```

That's it. No SDK changes, no code changes, no response format changes.

### Python SDK Example

```python
import anthropic

# Just change the base_url — everything else stays the same
client = anthropic.Anthropic(
    api_key="your-key",
    base_url="http://localhost:8888",  # ← router intercepts here
)

response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    messages=[{"role": "user", "content": "git status"}],
)
# This was handled locally for FREE — your app doesn't know the difference
```

### TypeScript/Node SDK Example

```typescript
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
  apiKey: "your-key",
  baseURL: "http://localhost:8888", // ← router intercepts here
});

const response = await client.messages.create({
  model: "claude-sonnet-4-20250514",
  max_tokens: 1024,
  messages: [{ role: "user", content: "run the tests" }],
});
// Handled locally — zero API cost
```

### cURL Example

```bash
curl http://localhost:8888/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-key" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "list the branches"}]
  }'
```

### Claude Code / Windsurf

```bash
export ANTHROPIC_BASE_URL=http://localhost:8888
# Then launch Claude Code or Windsurf normally
```

## API Compatibility

The router implements the full Anthropic Messages API contract:

| Feature | Supported | Notes |
|---------|-----------|-------|
| `POST /v1/messages` | ✅ | Full request/response format |
| `POST /v1/messages/count_tokens` | ✅ | Counts locally when model loaded, else forwards |
| Streaming (`stream: true`) | ✅ | SSE events in Anthropic format |
| Non-streaming | ✅ | JSON response |
| Tool use (`tool_use` / `tool_result`) | ✅ | Converts to Qwen format locally, returns Anthropic format |
| Auth passthrough (`x-api-key`) | ✅ | Forwards client auth or falls back to router's key |
| `authorization` header | ✅ | Forwarded transparently |
| `anthropic-version` header | ✅ | Forwarded (defaults to `2023-06-01`) |
| `anthropic-beta` headers | ✅ | Forwarded transparently |
| Unknown content blocks | ✅ | Graceful fallback — forwards raw request |
| Schema drift / new API features | ✅ | Parse failure → forward raw request to Claude |
| Thinking mode (`budget_tokens`) | ✅ | Hard-forwards to Claude (local can't do extended reasoning) |

## Routing Decisions

### What Gets Routed Where

| Your App Sends | Router Decision | Why | Cost |
|---------------|----------------|-----|------|
| Simple tool calls, short queries | **LOCAL** | Low forward score, default local | Free |
| CLI commands (`git status`, `npm install`) | **LOCAL** | Executable/action phrase detected | Free |
| "commit and push", "run the tests" | **LOCAL** | CLI action phrase | Free |
| Short questions, greetings | **LOCAL** | No forward signals, default local | Free |
| Code generation ("write a function") | **LOCAL** | Codegen score (+0.3) below threshold | Free |
| Complex reasoning, debugging | **CLAUDE** | Complexity patterns score ≥ 0.5 | Paid |
| "Explain how authentication works" | **CLAUDE** | Complexity pattern detected | Paid |
| "Refactor this module" | **CLAUDE** | Complexity pattern detected | Paid |
| Extended thinking (`budget_tokens`) | **CLAUDE** | Hard forward — local can't reason | Paid |
| Large context (>32K tokens) | **CLAUDE** | Context exceeds local limit | Paid |
| Model not loaded / unhealthy | **CLAUDE** | Fail-open safety | Paid |
| `@cloud` prefix in message | **CLAUDE** | User override | Paid |
| `@local` prefix in message | **LOCAL** | User override | Free |
| Unknown/new API features | **CLAUDE** | Graceful passthrough on parse failure | Paid |

### Forward Scoring

The default is **LOCAL**. Forward score starts at 0. Only when it reaches ≥ 0.5 does the request forward to Claude.

| Signal | Score | Example |
|--------|-------|---------|
| Complexity pattern | +0.5 | "explain", "refactor", "debug this bug" |
| Code generation request | +0.3 | "write a function", "scaffold", "optimize" |
| Extended conversation (>10 turns) | +0.4 | Long multi-turn sessions |
| Long message (>500 chars) | +0.2 | Detailed requests |
| Question chain (2+ `?`) | +0.2 | "what is this? why? how?" |
| Executable first word | -0.3 | `git status`, `docker ps` (reinforces local) |
| CLI action phrase | -0.3 | "commit and push", "run the tests" |
| Short message (<80 chars, no forward signals) | -0.1 | Simple commands |

## Safety Guarantees

### Fail-Open Design

The router **never degrades your app's capabilities**:

- **Schema drift tolerance**: If the request contains fields the router doesn't understand (new Anthropic features, new content block types), it forwards the raw request directly to Claude
- **Local generation failure**: If the local model errors during generation, the request is automatically retried against Claude
- **Model not loaded**: All requests forward to Claude until the local model is ready
- **Model unhealthy**: The watchdog monitors model health every 30s; if unhealthy, all requests forward to Claude
- **Auth passthrough**: The router forwards whatever API key your app sends, or uses its own configured key as fallback

### What the Local Model Can and Cannot Do

| Capability | Local (Qwen3-32B) | Claude API |
|-----------|-------------------|------------|
| Tool calling (function calls) | ✅ 75.7% BFCL v3 accuracy | ✅ Higher accuracy |
| Simple code generation | ✅ Good | ✅ Excellent |
| CLI task execution | ✅ Excellent | ✅ Excellent |
| Complex reasoning | ❌ Limited | ✅ Excellent |
| Extended thinking | ❌ Not supported | ✅ Supported |
| Long context (>32K) | ❌ Memory constrained | ✅ Up to 200K |
| Latest knowledge | ❌ Training cutoff | ✅ More current |

## Hardware Requirements

The router is optimized for **Apple Silicon Macs** with unified memory:

| Requirement | Minimum | Recommended (this project) |
|------------|---------|---------------------------|
| Chip | Apple M1 | **Apple M4 Max** |
| RAM | 16GB | **128GB** |
| Model | 7B 4-bit (~4GB) | **Qwen3-32B 4-bit (~19GB)** |
| Inference speed | ~10 tok/s | **~25-35 tok/s** |
| Memory bandwidth | 100 GB/s | **546 GB/s** |

## Monitoring

While the router is running, you can monitor its behavior:

```bash
# Overall stats (requests, cost savings)
curl http://localhost:8888/stats

# Performance metrics (latency, tokens/sec)
curl http://localhost:8888/perf

# Cache stats (hits, misses)
curl http://localhost:8888/cache

# Health check
curl http://localhost:8888/
```

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Configure
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Start the router
mlx-router serve

# 4. Point your app at it
export ANTHROPIC_BASE_URL=http://localhost:8888

# 5. Use your app normally — routing happens transparently
```

## Cost Impact

With aggressive local routing (default configuration), approximately **70-80% of requests** are handled locally at zero cost. Only complex reasoning, extended thinking, and large-context requests use the paid Claude API.

For a typical development session generating ~500 API calls:
- **Without router**: ~500 paid API calls
- **With router**: ~100-150 paid API calls + ~350-400 free local calls
- **Estimated savings**: 70-80% reduction in API costs
