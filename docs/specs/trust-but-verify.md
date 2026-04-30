# Trust-But-Verify (TBV) — QA/QC Routing Verification System

## Overview

A production quality gate that spot-checks routing decisions using Claude Opus as the
ground-truth validator. Runs primarily in **async background** mode (zero latency impact),
with an optional **shadow mode** that dual-generates for sampled requests to capture Opus's
actual output for comparison. The system feeds verification results back into the router
to dynamically optimize routing thresholds, signal weights, and trivial detection patterns.

## Architecture

```
                      ┌─────────────────────────────────────────────────┐
                      │              Normal Request Flow                 │
                      │  Request → classify() → LOCAL/FAST/FORWARD      │
                      │                         ↓                       │
                      │              User gets response immediately      │
                      └──────────────────────┬──────────────────────────┘
                                             │
                                    (sampled %)
                                             │
                      ┌──────────────────────▼──────────────────────────┐
                      │           TBV Verification Pipeline              │
                      │                                                  │
                      │  ┌─────────────┐   ┌──────────────────────────┐ │
                      │  │ Sample Gate │──▶│ Verification Queue        │ │
                      │  │ (adaptive)  │   │ (asyncio.Queue, bounded) │ │
                      │  └─────────────┘   └───────────┬──────────────┘ │
                      │                                 │                │
                      │                    ┌────────────▼─────────────┐  │
                      │                    │   Opus Validation Call    │  │
                      │                    │   (4-axis rubric judge)  │  │
                      │                    └────────────┬─────────────┘  │
                      │                                 │                │
                      │               ┌─────────────────▼──────────────┐│
                      │               │  Results Store + Router Tuning ││
                      │               │  (JSONL + dynamic adjustments) ││
                      │               └────────────────────────────────┘│
                      └─────────────────────────────────────────────────┘
```

## Verification Modes

### Mode 1: Async Background (Default)

- User gets response with zero latency impact.
- After response is sent, sampled requests are queued for Opus evaluation.
- Opus judges the local response using the full rubric (no shadow generation of its own output).

**Missed-Local Detection (forwarded requests)** — Three complementary strategies run in
parallel to identify requests that were forwarded but could have stayed local:

1. **Retroactive analysis** — After Opus responds, a follow-up judgment call asks Opus:
   *"Could a competent 27B local model have handled this equally well?"* Result stored
   with confidence score. Lowest cost, broadest coverage.

2. **Shadow local generation** — For sampled forwards, also generate locally in background.
   Then ask Opus to compare both responses on the full rubric. Proves empirically whether
   local CAN handle it. Medium cost, strongest evidence.

3. **Heuristic targeting** — Specifically flag forwarded requests where `fwd_score` was
   close to the routing threshold (score between `threshold - 0.1` and `threshold`).
   These borderline cases are most likely to be "missed local" opportunities. They get
   priority in the verification queue and higher sampling rate (2x base rate).

All three strategies feed into the same `verify_tuner` — findings are weighted by
confidence: shadow local > retroactive > heuristic.

### Mode 2: Shadow (Optional, Activatable On-Demand)

- For sampled local requests: also forward the request to Opus in parallel.
- User still sees local response immediately.
- TBV captures both outputs and asks Opus to compare them using the rubric.
- Proves semantic equivalence with hard evidence (actual Opus output).
- Higher cost but strongest signal for router tuning.
- For sampled forwards: also generate locally in background, then compare.
- Activate via `POST /verify/enable {"shadow": true}` or `VERIFY_SHADOW_MODE=true`.

## Adaptive Sampling Rate

| Condition | Sample Rate |
|-----------|-------------|
| Cold start (< 50 verified) | 20% |
| Stable (pass rate > 90%) | 5% |
| Degrading (pass rate < 85%) | 15% |
| After routing change detected | 30% for 20 requests, then back to stable |
| Manual override | `VERIFY_SAMPLE_RATE` env var (0.0–1.0) |
| Off | `VERIFY_ENABLED=false` |

Sampling applies independently to local and forwarded requests.

## 4-Axis Rubric (Opus Evaluation)

Opus receives the original request + local response (+ Opus response in shadow mode)
and returns structured JSON:

```json
{
  "correctness": {
    "score": 4,
    "explanation": "Code is syntactically correct but misses edge case for empty input"
  },
  "completeness": {
    "score": 5,
    "explanation": "All requested functionality is present"
  },
  "code_quality": {
    "score": 4,
    "explanation": "Clean code but could use better variable naming"
  },
  "routing_appropriateness": {
    "score": 5,
    "explanation": "This is a simple git command, correctly handled locally"
  },
  "overall_pass": true,
  "could_be_local": true,
  "suggested_route": "local",
  "confidence": 0.95
}
```

Scoring: 1–5 per axis. `overall_pass` = true if all axes ≥ 3.

## Router Auto-Tuning (Feedback Loop)

Verification results feed back into the router via a dedicated tuning module:

### Signal Adjustments

| Verification Finding | Router Action |
|---------------------|---------------|
| Local response failed (score < 3 on any axis) | Increase forward signal weights for matching pattern |
| Local response perfect (all axes = 5) | Decrease forward signal weights (keep more local) |
| Forwarded request judged "could be local" | Lower routing threshold slightly |
| Forward needed but scored local | Increase routing threshold slightly |
| Trivial request failed locally | Remove from trivial patterns or raise threshold |
| Fast model failed but main would pass | Route to main model instead of fast |

### Adjustment Mechanics

- Adjustments are small (±0.01–0.05 per finding) and exponentially smoothed.
- A `verify_adjustments` dict in annealing tracks per-signal corrections.
- Adjustments decay toward 0 over time if not reinforced (half-life: 200 verifications).
- Router applies `verify_adjustments` on top of existing annealing adjustments.
- Hard bounds prevent any single adjustment from exceeding ±0.3.

### Pattern Learning

When multiple similar requests fail verification:
1. Extract common keywords/patterns from failed requests.
2. Add to a `_LEARNED_FORWARD_PATTERNS` list with associated weight.
3. Patterns decay if not reinforced by subsequent failures.

## Config

| Variable | Default | Description |
|----------|---------|-------------|
| `VERIFY_ENABLED` | `false` | Enable trust-but-verify system |
| `VERIFY_SAMPLE_RATE` | `0.0` | Override adaptive rate (0.0 = use adaptive, >0 = fixed rate) |
| `VERIFY_SHADOW_MODE` | `false` | Enable shadow mode (dual-generate) |
| `VERIFY_MODEL` | `claude-sonnet-4-20250514` | Model used for verification judgments |
| `VERIFY_QUEUE_SIZE` | `50` | Max pending verification tasks |
| `VERIFY_AUTO_TUNE` | `true` | Allow verification to auto-adjust router weights |
| `VERIFY_MIN_SCORE` | `3` | Minimum acceptable score per axis (below = fail) |
| `VERIFY_ALERT_WEBHOOK` | `""` | URL to POST alerts when pass rate drops (empty = disabled) |

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/verify` | TBV status — enabled, mode, sample rate, pass rates, queue depth |
| `GET` | `/verify/results` | Recent verification results (limit param) |
| `GET` | `/verify/adjustments` | Current router adjustments from verification |
| `POST` | `/verify/enable` | Enable/disable TBV or switch mode |
| `POST` | `/verify/reset` | Clear all verification data and adjustments |

## Files

| File | Purpose |
|------|---------|
| `src/mlx_task_router/verify.py` | Core TBV engine: sampling, queue, Opus calls, rubric parsing |
| `src/mlx_task_router/verify_tuner.py` | Router auto-tuning from verification results |
| `tests/test_verify.py` | Unit tests for sampling, rubric, queue, tuning |
| `tests/test_verify_tuner.py` | Unit tests for adjustment mechanics, decay, bounds |

## Data Storage

- **Verification log**: `~/.config/mlx-task-router/verify_log.jsonl`
  - Each entry: timestamp, request_hash, route, scores, pass/fail, adjustments applied
- **Adjustments state**: `~/.config/mlx-task-router/verify_adjustments.json`
  - Persisted to survive restarts; loaded on startup

## Implementation Order

1. Config additions (6 new env vars)
2. `verify.py` — TBV engine (sampling gate, queue, Opus validation call, rubric parsing)
3. `verify_tuner.py` — Auto-tuning logic (signal adjustment, decay, bounds, pattern learning)
4. Wire into `server.py` — Queue sampled requests post-response
5. Wire into `router.py` — Apply verify_adjustments in scoring
6. Endpoints — `/verify/*`
7. Tests — Unit tests for both modules
8. Dashboard update — Add verification pass rate card

## Opus Validation Prompt Template

```
You are a routing quality auditor for an AI coding assistant. Your job is to evaluate
whether a local language model (Qwen3.6-27B running on Apple Silicon) produced an
acceptable response to a coding request.

ORIGINAL REQUEST:
{request}

LOCAL MODEL RESPONSE:
{local_response}

{shadow_section}

Evaluate the local response on these 4 axes (score 1-5):
1. **Correctness** — Is the code/answer factually correct? Any bugs or errors?
2. **Completeness** — Does it address all parts of the request?
3. **Code Quality** — Is it well-structured, readable, follows best practices?
4. **Routing Appropriateness** — Was this request appropriate for a local 27B model,
   or should it have been forwarded to a frontier model?

Also determine:
- `could_be_local` (bool): Could a competent 27B local model handle this?
- `suggested_route`: "local", "fast", or "forward"
- `confidence` (0-1): How confident are you in this assessment?

Return ONLY valid JSON matching this schema:
{schema}
```

## Risks & Mitigations

- **Cost** — Shadow mode doubles API cost for sampled requests. Mitigated by adaptive
  sampling that decreases rate as confidence grows.
- **Latency on shadow** — Opus call runs in parallel/background; never blocks user response.
- **Feedback loops** — Adjustments are bounded (±0.3) and decay. Cannot spiral.
- **Opus availability** — If Opus call fails, drop the verification (don't retry).
  Queue has bounded size to prevent memory growth.
- **Privacy** — Verification log contains request/response pairs. Same security
  posture as existing routing_history.py. Configurable retention.

## Self-Annealing Integration

The verify_tuner integrates with the existing annealing system:
- `annealing.py` checks `verify_adjustments` alongside its own adjustments.
- Both systems operate on the same signal weights but with separate tracking.
- Verify adjustments take precedence when they have high confidence (many samples).
- Annealing can override verify adjustments if feedback data contradicts them.
