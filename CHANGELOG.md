# Changelog

## [1.2.0] — 2026-05-01

### Quality Assurance Gate — Guaranteed Routing Quality

Pre-delivery confidence gate that ensures local responses match Claude quality.
Uncertain requests (forward score in the gray zone) are shadow-validated before
delivery: both local and Claude generate in parallel, then a validation model
judges equivalence. If local fails, Claude's response is transparently swapped in.
Graduated trust narrows the gate over time as categories prove reliable.

**Confidence-Gated Routing** (`qa_gate.py`):
- Three zones: Green (confident local → deliver immediately), Yellow (uncertain →
  shadow validate), Red (confident forward → send to Claude)
- Parallel generation: local model and Claude run simultaneously
- Validation model judges equivalence with structured JSON rubric
- Fail-open: on timeout or error, local response is delivered
- Zero impact on streaming requests (gate applies to non-streaming only)

**Graduated Trust** (`qa_trust.py`):
- Five trust levels: Unproven → Building → Trusted → Proven → Degraded
- Per-category tracking with Wilson score 95% confidence intervals
- Proven categories (100+ samples, ≥98% pass) bypass the gate entirely
- Trusted categories (50+ samples, ≥95% pass) get narrowed gate (±0.1)
- Degraded categories (pass rate <90%) get widened gate (±0.1)
- Evidence persisted to `~/.config/mlx-task-router/qa_evidence.json`

**Evidence Dashboard** (`qa_dashboard.py`):
- Dedicated dashboard at `/qa/dashboard` — linked from main dashboard
- Hero quality score with 95% confidence interval
- Per-category evidence table (trust level, samples, pass rate, CI, failures)
- Trust level doughnut chart and pass rate bar chart
- Gate configuration visualizer (green/yellow/red zones)
- Cost analysis: gate hits, bypasses, swaps, shadow spend
- Auto-refresh every 5 seconds

**Server Integration**:
- Non-streaming local requests in the uncertain zone are routed through
  `_handle_gated()` which runs parallel generation + validation
- Confident local requests record a bypass for cost tracking
- 8 new endpoints: `GET /qa`, `GET /qa/categories`, `GET /qa/evidence`,
  `GET /qa/failures`, `POST /qa/enable`, `POST /qa/reset`, `GET /qa/cost`,
  `GET /qa/dashboard`
- Config exposed in `GET /config` response
- Server version bumped to 1.2.0

**Config** (9 new env vars):
- `QA_GATE_ENABLED` (bool, default: false)
- `QA_GATE_LOWER` (float, default: 0.3)
- `QA_GATE_UPPER` (float, default: 0.7)
- `QA_GATE_TIMEOUT` (int, default: 10)
- `QA_GATE_VALIDATION_MODEL` (string, default: claude-sonnet-4-20250514)
- `QA_TRUST_MIN_SAMPLES` (int, default: 50)
- `QA_TRUST_PROVEN_SAMPLES` (int, default: 100)
- `QA_TRUST_PASS_THRESHOLD` (float, default: 0.95)
- `QA_TRUST_PROVEN_THRESHOLD` (float, default: 0.98)

### How Quality is Proven

The QA dashboard displays a single quality assurance score:

> Quality Assurance: 97.3% [95% CI: 95.1% – 98.8%]
> Based on 482 validated responses across 9 categories

This means: "With 95% statistical confidence, at least 95.1% of local responses
are functionally equivalent to what Claude would produce."

Categories progress from Unproven → Proven as evidence accumulates. Once a
category reaches Proven status, it bypasses the gate entirely — the router has
statistically demonstrated that local handles it correctly.

### Testing

- **379 tests pass** (was 332), 7 skipped
- New: `test_qa_trust.py` — 25 tests (trust levels, evidence, gate bounds, quality score)
- New: `test_qa_gate.py` — 14 tests (request extraction, JSON parsing, gate delegation)
- Extended: `test_server.py` — 8 new tests for `/qa/*` endpoints

### Files Changed

- **New**: `src/mlx_task_router/qa_gate.py`, `src/mlx_task_router/qa_trust.py`,
  `src/mlx_task_router/qa_dashboard.py`
- **New**: `tests/test_qa_gate.py`, `tests/test_qa_trust.py`
- **New**: `docs/specs/quality-assurance-v2.md` (implementation spec)
- **Modified**: `config.py`, `server.py`, `dashboard.py`
- **Modified**: `.env.example`, `tests/test_server.py`

---

## [1.1.0] — 2026-05-01

### Trust-But-Verify (TBV) — QA/QC Routing Verification

Production quality gate that spot-checks routing decisions using Claude as ground-truth
validator. Runs async in background with zero latency impact. Feeds results into the
router to dynamically optimize thresholds and signal weights.

**Core Engine** (`verify.py`):
- `TBVEngine` class with async background worker and bounded queue
- Adaptive sampling: 20% cold start → 5% stable → 15% degrading → 30% burst on change
- Full 4-axis rubric: correctness, completeness, code quality, routing appropriateness
- Two verification modes:
  - **Async background** (default): validates local responses post-delivery
  - **Shadow mode** (on-demand): dual-generates for direct comparison
- Three missed-local detection strategies:
  - Retroactive analysis (asks Opus if local could have handled forwarded requests)
  - Shadow local generation (generates locally for sampled forwards, then compares)
  - Heuristic targeting (2x sample rate for borderline forwards near threshold)
- Results persisted to `~/.config/mlx-task-router/verify_log.jsonl`

**Auto-Tuning** (`verify_tuner.py`):
- `VerifyTuner` applies bounded adjustments (±0.02/result, max ±0.3) to signal weights
- Exponential decay (half-life: 200 verifications) prevents stale adjustments
- Actions: increase_forward, decrease_forward, lower_threshold, raise_threshold, reduce_trivial
- Threshold adjustments applied in `classify()` with hard bounds [0.3, 1.0]
- Signal adjustments applied in `_score_forward()` for complexity and codegen signals
- State persisted to `~/.config/mlx-task-router/verify_adjustments.json`

**Server Integration**:
- TBV engine starts/stops with server lifespan (when `VERIFY_ENABLED=true`)
- Local non-streaming responses queued for verification (sampled)
- Forwarded non-streaming responses queued for retroactive analysis (sampled)
- 5 new endpoints: `GET /verify`, `GET /verify/results`, `GET /verify/adjustments`,
  `POST /verify/enable`, `POST /verify/reset`
- `POST /verify/enable` accepts `{"enabled": bool, "shadow": bool}` for runtime toggle
- Config exposed in `GET /config` response

**Dashboard**:
- New "TBV Pass" stat card (rose-400 color)
- Config panel shows TBV status (on/off with total checks count)

**Config** (8 new env vars):
- `VERIFY_ENABLED` (bool, default: false)
- `VERIFY_SAMPLE_RATE` (float, 0.0 = adaptive)
- `VERIFY_SHADOW_MODE` (bool, default: false)
- `VERIFY_MODEL` (string, default: claude-sonnet-4-20250514)
- `VERIFY_QUEUE_SIZE` (int, default: 50)
- `VERIFY_AUTO_TUNE` (bool, default: true)
- `VERIFY_MIN_SCORE` (int, default: 3)
- `VERIFY_ALERT_WEBHOOK` (string, empty = disabled)

### Testing

- **332 tests pass** (was 280), 7 skipped
- New: `test_verify.py` — 27 tests (sampling, results, prompts, queue, reset)
- New: `test_verify_tuner.py` — 20 tests (actions, bounds, decay, reset, status)
- Extended: `test_server.py` — 5 new tests for `/verify/*` endpoints

### Files Changed

- **New**: `src/mlx_task_router/verify.py`, `src/mlx_task_router/verify_tuner.py`
- **New**: `tests/test_verify.py`, `tests/test_verify_tuner.py`
- **New**: `docs/specs/trust-but-verify.md` (implementation spec)
- **Modified**: `config.py`, `router.py`, `server.py`, `dashboard.py`
- **Modified**: `.env.example`, `tests/test_server.py`

---

## [1.0.0] — 2026-05-01

### Embedding-Based Semantic Routing

Implements embedding-based routing to augment regex heuristics with vector similarity.
The local model's embedding layer extracts a dense representation of each request, and
a lightweight linear probe classifies difficulty. Gracefully degrades when model not
loaded or insufficient training data exists.

- **New file: `embed_router.py`** — `EmbedRouter` class with `embed()`, `score()`,
  `train()`, `record_example()`, probe save/load, and status reporting
- **Router integration** — Embedding signal added to `_score_forward()` via
  `embed_router.score()`. Weight controlled by `EMBED_WEIGHT` config (default 0.3)
- **Training pipeline** — Every request records a training example (JSONL). The
  annealing thread triggers probe retraining when sample count exceeds `EMBED_MIN_SAMPLES`
- **Model attachment** — `ModelManager.load_model()` attaches the model to the embed
  router; `unload()` detaches. Probe persists across restarts via `embed_probe.json`
- **Config**: `EMBED_ROUTING` (bool), `EMBED_WEIGHT` (float), `EMBED_MIN_SAMPLES` (int)
- **Endpoints**: `GET /embed` — probe status and training sample count

### Multi-Model Pool (3-Tier Routing)

Implements tiered routing: trivial CLI/edit requests → fast 1.5B model, coding/analysis
→ main 27B model, complex stacked-signal requests → Claude Opus.

- **New file: `model_pool.py`** — `ModelPool` class wrapping fast + main model slots.
  Delegates to `ModelManager` for main model (backward compat). Fast model loaded
  independently with its own sampler
- **New route: `Route.FAST`** — Added to `Route` class. Trivial requests detected by
  `_is_trivial()` using 5 regex pattern groups (git/shell commands, package managers,
  simple edits, view commands, infra tools). Max 100 chars
- **Tier selection in `classify()`** — When `MLX_FAST_MODEL` is configured and
  `fwd_score < TRIVIAL_THRESHOLD` and `_is_trivial()` matches → FAST route
- **Server wiring** — `_handle_local()` accepts `tier` param, selects `_gen_func`/
  `_stream_func` based on tier. Stats recorded via `record_fast()` for fast tier
- **Stats**: `record_fast()` method, `requests_fast` counter in stats output
- **Config**: `MLX_FAST_MODEL` (string), `FAST_MODEL_MAX_TOKENS` (int, default 2048),
  `TRIVIAL_THRESHOLD` (float, default 0.3)
- **Endpoints**: `GET /pool` — pool status (main/fast model availability)

### Bug Fix

- **`_yield_events` restored** — Cache replay for streaming responses was broken since
  v0.9.0 streaming refactor. Added back as async generator

### Dashboard

- **Fast tier support** — New "Fast Reqs" stat card, cyan badge for fast route, 4-slice
  doughnut chart (Local/Fast/Forward/Cache)
- **Embed/Pool status** — Config panel shows embed probe readiness (collecting/ready with
  sample count) and fast pool status (loaded/off)
- **Grid updated** — 7 stat cards across full width on large screens

### Documentation

- **README.md** — Added 19 missing API endpoints across 3 new sections (Routing
  Intelligence, Sessions, Operations). Added Embedding Routing and Multi-Model Pool
  config reference tables
- **.env.example** — Added all 6 new config variables with documentation

### Testing

- **280 tests pass** (was 243), 7 skipped
- New: `test_embed_router.py` — 14 tests (cold start, training, probe persistence, scoring)
- New: `test_model_pool.py` — 10 tests (init, tier routing, fast model management)
- Extended: `test_router.py` — 10 new tests for `_is_trivial()` and FAST route classification
- Extended: `test_benchmark.py` — Updated to accept `Route.FAST` as local-equivalent
- Extended: `test_server.py` — Tests for `/embed` and `/pool` endpoints
- Extended: `test_config.py` — Tests for new config fields

### Files Changed

- **New**: `src/mlx_task_router/embed_router.py`, `src/mlx_task_router/model_pool.py`
- **New**: `tests/test_embed_router.py`, `tests/test_model_pool.py`
- **Modified**: `config.py`, `router.py`, `local.py`, `server.py`, `stats.py`,
  `annealing.py`, `test_router.py`, `test_benchmark.py`, `test_server.py`,
  `test_config.py`

---

## [0.9.0] — 2026-04-30

### Performance Optimizations

Five high-impact changes targeting inference speed, streaming latency, and routing intelligence.

**1. Sampler caching** — `_build_sampling_args()` was called on every request, re-importing
`mlx_lm.sample_utils` and reconstructing sampler objects each time. Now built once at
`load_model()` and reused as `self._sampler` / `self._logits_processors`. Eliminates
per-request import overhead.

**2. Speculative decoding enabled** — `MLX_DRAFT_MODEL=mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit`
activated in production config. Draft model predicts 5 candidate tokens per step, verified
in batch by main model. Expected 1.5-2x throughput improvement for ~1GB extra VRAM.

**3. Real-time streaming** — Replaced buffered streaming (`_collect_local_stream` collected
ALL events before yielding) with `asyncio.Queue`-based pipeline. Generator thread pushes
tokens into queue immediately; async SSE response pops and yields in real-time. Client now
sees first token in ~200ms instead of waiting for full generation.

**4. KV-cache reuse** — Integrated mlx-lm's `make_prompt_cache()` for cross-turn prompt
caching. Multi-turn conversations reuse the KV-state from shared prefixes, skipping
redundant prefill computation. Protected by `threading.Lock` for concurrent safety.

**5. Annealing wired to routing** — `WeightAnnealer` computed adjustments but they were
never applied to signal weights. Added `_get_weight(base, category)` that applies annealing
adjustments inline. Removed old double-counting block. Annealing now actually auto-tunes
routing weights from feedback data.

**Implementation specs written:**
- `docs/specs/embedding-routing.md` — Replace regex heuristics with embedding-based classifier
- `docs/specs/multi-model-pool.md` — 3-tier routing: 1.5B (trivial) / 27B (coding) / Opus (hard)

### Files Changed
- `local.py` — sampler caching, KV-cache reuse, prompt cache init, threading lock
- `server.py` — real-time streaming via asyncio.Queue, version bump to 0.9.0
- `router.py` — `_get_weight()` helper, annealing wired to all signal weights
- `test_config.py` — fix draft_model env leak in defaults test
- `~/.config/mlx-task-router/.env` — speculative decoding enabled
- `docs/specs/embedding-routing.md` — new implementation spec
- `docs/specs/multi-model-pool.md` — new implementation spec

### Test Results
243 passed, 7 skipped.

---

## [0.8.0] — 2026-04-30

### Aggressive Routing for Qwen3.6-27B

Routing weights retuned to leverage Qwen3.6-27B's 77.2% SWE-bench capability.
Single-signal complexity or codegen requests now stay local — only stacked
signals forward to Claude Opus 4.6. Target: push local routing from ~87% to 93-95%.

**Weight changes:**
- `ROUTING_THRESHOLD`: 0.5 → **0.7** (requires multiple signals to forward)
- `FWD_COMPLEXITY`: 0.5 → **0.4** (single complexity alone stays local)
- `FWD_CODE_GENERATION`: 0.3 → **0.2** (Qwen3.6-27B handles codegen well)
- `FWD_EXTENDED_CONVO`: 0.4 → **0.3**, turn threshold 20 → **30**
- `FWD_LONG_MSG`: 0.2 → **0.15**
- `FWD_QUESTION_CHAIN`: 0.2 → **0.15**
- `FWD_MANY_TOOLS`: 0.2 → **0.15**
- `FWD_VERY_MANY_TOOLS`: 0.4 → **0.3**

**What stays local now (was forwarded):**
- "Explain how X works" — single complexity
- "Refactor this module" — single complexity
- "Debug this memory leak" — single complexity
- "Write a function for X" — single codegen

**What still forwards to Opus:**
- Stacked signals: complexity + codegen + questions (score ≥ 0.7)
- Extended thinking (budget_tokens)
- Context too large, model not loaded, @cloud override

**Codegen regex fix:** Pattern now allows optional adjectives (e.g. "Create a **new** module" matches).

**Benchmark:** 55 fixtures, 243 tests pass (7 skipped). New `complex_multi` category added.

### Files Changed
- `router.py` — weights, threshold, docstring, turn threshold, codegen regex
- `config.py` — default ROUTING_THRESHOLD 0.5 → 0.7
- `.env.example` — threshold and documentation updated
- `benchmark_requests.json` — 11 complex fixtures reclassified local, 3 complex_multi forward added
- `test_router.py` — updated for new routing behavior
- `test_benchmark.py` — complex category now tests LOCAL, complex_multi tests FORWARD
- `test_adaptive_routing.py` — uses FORWARD_THRESHOLD constant

---

## [0.7.0] — 2026-04-29

### Model Upgrade: Qwen3.6-27B-OptiQ-4bit

Default model switched from `mlx-community/Qwen3-Coder-Next-4bit` (MoE 80B/3B) to `mlx-community/Qwen3.6-27B-OptiQ-4bit` (Dense 27B).

**Why:**
- **SWE-bench Verified: 77.2%** (was 70.6%) — +6.6% improvement
- **Terminal-Bench 2.0: 59.3%** — matches Claude 4.5 Opus exactly
- **~2x faster inference** — dense 27B vs MoE 80B on Apple Silicon
- **~18GB VRAM** (was ~48GB) — runs on 32GB+ machines, frees memory for other workloads
- **Thinking Preservation** — retains reasoning across multi-turn agent sessions
- **128K native context** — sufficient for nearly all coding sessions
- **Compatible with stock mlx-lm** — no special dependencies needed

**Sampling params updated:**
- `MLX_TEMPERATURE`: 1.0 → 0.6
- `MLX_TOP_K`: 40 → 20
- `MLX_TOP_P`: 0.95 (unchanged)

### Files Changed
- `config.py` — DEFAULT_MODEL updated
- `.env.example` — model, sampling docs, and comments updated
- `README.md` — configuration reference tables updated

---

## [0.6.1] — 2026-04-25

### Per-Session Stats

Track routing patterns grouped by coding session for optimization insights.

- **Auto-session detection** — requests within 5 minutes of each other are grouped; gap > 5 min starts a new session. Configurable via `SESSION_GAP_SECONDS`.
- **Header-based sessions** — reads `x-session-id`, `anthropic-session-id`, or `x-request-id` prefix from request headers for explicit session identification.
- **Per-session metrics** — request counts (local/forward/cache), token usage, cost saved, top routing triggers, recent decisions (last 50 per session).
- **Ring buffer** — last 50 sessions in memory, no disk persistence.
- **5 new endpoints**: `GET /sessions`, `GET /sessions/current`, `GET /sessions/summary`, `GET /sessions/{id}`, `POST /sessions/clear`

### Routing Dashboard

Live web UI at `GET /dashboard` — no npm, no build step, served inline by FastAPI.

- **Summary cards** — total requests, local %, cost saved, tok/s, P50 latency, session count
- **Routing distribution chart** — doughnut chart (local / forward / cache) via Chart.js
- **Performance panel** — routing avg, gen avg, forward avg, P95/P99 latency, requests/hour
- **Config display** — temperature, top-p, top-k, max tokens, context limit, threshold
- **Recent decisions table** — last 30 routing decisions with time, route, score, trigger, preview
- **Session breakdown** — expandable cards per session with triggers and recent decisions
- **Auto-refresh** — 5-second polling interval, live indicator

### Bug Fixes

- **Critical: mlx-lm 0.31 API incompatibility** — `generate_step()` in mlx-lm 0.31+ no longer accepts `temp`, `top_p`, `top_k`, `repetition_penalty` as direct kwargs. Every local generation attempt crashed with `generate_step() got an unexpected keyword argument 'temp'`, silently falling back to Claude API. Fixed by building proper `sampler` (via `make_sampler()`) and `logits_processors` (via `make_logits_processors()`) objects from `mlx_lm.sample_utils`.
- **Streaming cost savings not recorded** — `stats.record_local()` was only called in the non-streaming path of `_handle_local`. Claude Code uses streaming exclusively, so all locally-handled requests showed $0.00 cost savings. Fixed by adding `stats.record_local()` to the streaming path alongside `perf_metrics.record()`.
- **`mlx-router-ctl status`** — fixed garbled Requests line (`requests_forward` → `requests_forwarded`)

### Testing

- **241 passed, 7 skipped** (was 207 — +34 new tests)
- New: 24 session stats unit tests, 7 session endpoint tests, 3 dashboard tests

### Files Changed
- `session_stats.py` — **new** — SessionTracker + SessionStats classes
- `dashboard.py` — **new** — HTML template + APIRouter
- `local.py` — replaced raw temp/top_p/top_k/repetition_penalty kwargs with sampler + logits_processors for mlx-lm 0.31+
- `server.py` — session tracking wired into /v1/messages, session + dashboard endpoints, streaming stats fix
- `test_session_stats.py` — **new** — 24 tests
- `test_server.py` — +10 tests (7 session, 3 dashboard)
- `install.sh` — bug fix in status display

---

## [0.6.0] — 2026-04-25

### Model Upgrade: Qwen3-Coder-Next-4bit

**Breaking change**: Default MLX model upgraded from `mlx-community/Qwen3-32B-4bit` (dense 32B, ~19GB) to `mlx-community/Qwen3-Coder-Next-4bit` (MoE 80B/3B-active, ~48GB). Requires **128GB unified memory** — will not fit on 64GB machines.

#### Why this model
- **Purpose-built for agentic coding** — trained specifically for Claude Code, Cline, Qwen Code IDE scaffolds
- **SWE-bench Verified: 70.6%** — massive improvement over Qwen3-32B (not benchmarked on SWE-bench)
- **MoE architecture**: 80B total parameters, only 3B active per token → 80B knowledge at ~8B inference cost
- **256K native context** (was 32K) — holds entire small codebases in working memory
- **Agent recovery training** — explicitly trained to recover from execution failures
- **22-28 tok/s on M4 Max 128GB** — slightly slower than 25-35 tok/s but acceptable for quality gains

#### Generation parameter changes
| Parameter | Old (Qwen3-32B) | New (Qwen3-Coder-Next) |
|-----------|-----------------|------------------------|
| `MLX_TEMPERATURE` | 0.7 | **1.0** |
| `MLX_TOP_P` | 0.8 | **0.95** |
| `MLX_TOP_K` | 20 | **40** |
| `MLX_MAX_TOKENS` | 8192 | **16384** |
| `MAX_LOCAL_CONTEXT_TOKENS` | 32000 | **65536** |

### Routing Refinement

- **Removed `optimize|improve|enhance|speed up|performance` from codegen forward signal** — purpose-built coding model handles these locally
- **Added tool-count forward signal**: >15 tool definitions → +0.2, >30 → +0.4 (many tools harder for local models)
- **Raised extended conversation threshold**: >10 → **>20 user turns** to account for tool-call-heavy sessions
- **Tool results excluded from turn counting**: `tool_result` messages no longer count as user turns in `_count_turns()` — they're automated round-trips, not genuine user complexity
- **Updated docstring** to clarify "default LOCAL" (routing preference) vs "fail-open" (error handling)

### Performance & Safety

- **Generation timeout**: Configurable `MLX_GENERATION_TIMEOUT` (default 120s). If local model hangs, request auto-forwards to Claude API. Closes the last fail-open gap.
- **Prompt KV cache fix**: Cache now returns immediately on hash match without re-rendering the template — eliminates redundant `apply_chat_template` calls
- **mlx-lm version check**: Warns on startup if mlx-lm < 0.30.5 (required for MoE support)
- **Graceful `enable_thinking` handling**: Uses try/except for models that don't support the kwarg (Qwen3-Coder-Next has no thinking mode)

### Testing

- **207 passed, 7 skipped** (was 202 passed, 7 skipped — +5 new tests)
- New tests: `test_optimize_not_codegen`, `test_extended_conversation_under_threshold`, `test_many_tools_signal`, `test_very_many_tools_signal`, `test_tool_result_excluded`
- Updated: config default tests, codegen pattern tests, extended conversation tests, benchmark fixtures

### Files Changed
- `config.py` — new defaults (model, tokens, generation params, context limit)
- `local.py` — mlx-lm version check, graceful enable_thinking, KV cache fix
- `router.py` — removed optimize patterns, tool-count signal, _count_turns fix, >20 threshold
- `server.py` — generation timeout, version 0.6.0
- `.env.example` — updated all defaults + model documentation
- `tests/test_config.py` — updated for new defaults
- `tests/test_router.py` — 5 new tests, updated existing
- `tests/test_integration.py` — updated memory comment
- `REASSESSMENT.md` — comprehensive v2 audit document

---

## [0.5.1] — 2025-07-25

### System-Level Service Scripts

#### install.sh — Full System Installer
Comprehensive installation script that sets up mlx-task-router as a system-level macOS service:
- **Preflight checks**: Validates macOS, Apple Silicon, Python ≥ 3.11, mlx-lm, existing .venv
- **System install**: Copies project + venv to `/opt/mlx-task-router` via rsync
- **Config management**: Creates `~/.config/mlx-task-router/.env` from `.env.example` (preserves existing)
- **Log directory**: Creates `/var/log/mlx-task-router/` with stdout/stderr log files
- **LaunchDaemon**: Installs system-level plist at `/Library/LaunchDaemons/com.sealmindset.mlx-task-router.plist`
  - Runs at boot (before user login), auto-restarts on crash
  - Runs as your user (not root), with `Nice=-5` priority
  - `KeepAlive` on unexpected exit, 10s throttle between restarts
- **mlx-router-ctl**: Installs service management command to `/usr/local/bin/`
- **Shell profile**: Adds `ANTHROPIC_BASE_URL` export to `.zshrc`
- **Health wait**: Polls `/health` endpoint for up to 90s after start
- Flags: `--no-start`, `--upgrade`

#### start.sh — Service Management
Full-featured service control script with 9 commands:
- `start` — Bootstrap LaunchDaemon, wait for health (90s timeout)
- `stop` — Bootout LaunchDaemon gracefully
- `restart` — Stop + start with health wait
- `status` — Detailed view: service state, PID, config, health, stats, last 5 log lines
- `logs` — Tail live stdout + stderr
- `health` — Quick JSON health check
- `test` — Smoke test all 12 endpoints + message routing test
- `foreground` — Run without launchd (dev/debug mode)
- `install-check` — Verify all installation components are present and correct

#### uninstall.sh — Updated
Updated to handle both system-level LaunchDaemon and legacy user-level LaunchAgent:
- Removes `/opt/mlx-task-router` install directory
- Removes `/usr/local/bin/mlx-router-ctl`
- Removes `/var/log/mlx-task-router/` log directory
- Removes both `/Library/LaunchDaemons/` and `~/Library/LaunchAgents/` plists
- Added Qwen3-32B-4bit to model cleanup list

### File Layout After Install
```
/opt/mlx-task-router/              — Project files + .venv
/Library/LaunchDaemons/com.sealmindset.mlx-task-router.plist
/var/log/mlx-task-router/          — stdout.log, stderr.log
/usr/local/bin/mlx-router-ctl      — Service management CLI
~/.config/mlx-task-router/.env     — Configuration
```

---

## [0.5.0] — 2025-07-25

### Medium Priority Features — All Complete

#### M1: Real-Time Token Streaming
Rewrote `stream_generate()` in `local.py` to emit SSE text delta events **in real-time** as tokens arrive from MLX. Previously, all tokens were buffered, then sliced into fake 12-char chunks. Now:
- Text tokens stream immediately to the client — no artificial delay
- When a `<tool_call>` tag is detected, streaming switches to buffer mode
- Tool call blocks are emitted as complete content blocks after buffering
- Result: users see text appear instantly instead of waiting for full generation

#### M2: Routing Decision History
New `routing_history.py` module with a 100-entry ring buffer. Every routing decision is recorded with:
- Forward score, individual signals, trigger name
- Message preview (first 80 chars)
- Model name and timestamp

Endpoints: `GET /routing/history?limit=50`, `GET /routing/summary`, `POST /routing/clear`

#### M3: Integration Tests with Real Model
New `test_integration.py` with 7 tests gated behind `MLX_AVAILABLE` and `SKIP_INTEGRATION=0`:
- Model loading and token counting
- Simple generation with response structure validation
- Content block type checking
- Streaming SSE event structure
- Streaming JSON parsing
- Tool calling with Bash tool
- Run with: `SKIP_INTEGRATION=0 pytest tests/test_integration.py -v`

#### M4: Config Reload Endpoint
Added `reload()` method to `Config` class that:
- Clears dotenv cache and re-reads `.env` files
- Detects changed fields and returns them
- Auto-reloads model if `MLX_MODEL` changed

Endpoints: `GET /config` (current settings), `POST /config/reload` (re-read .env)

#### M5: Prompt KV Cache
Added system prompt template caching to `ModelManager`:
- Caches rendered chat templates keyed by SHA-256 hash of system prompt + tools
- Avoids re-encoding identical system prompts on every request
- Cache is per-model, cleared on model reload

#### M6: Semantic Response Cache
New `semantic_cache.py` module using character-level n-gram Jaccard similarity:
- No external dependencies — pure Python trigram comparison
- Default threshold: 0.85 (configurable via `SEMANTIC_CACHE_THRESHOLD`)
- Falls back to exact-match cache first, then tries semantic match
- Tracks hits, misses, and near-misses
- Both exact and semantic caches populated on local generation

Endpoints: `GET /semantic-cache`, `POST /semantic-cache/clear`

#### M7: Self-Annealing Routing Weights
New `annealing.py` module with gradient-free weight optimization:
- Background thread runs every 5 minutes (configurable)
- Analyzes feedback data: high failure rates increase forward signal weights, consistent local success decreases them
- Weight adjustments bounded between -1.0 and +1.0
- Persisted to `~/.config/mlx-task-router/annealing.json`
- Applied in `_score_forward()` after all other signals

Endpoints: `GET /annealing`, `POST /annealing/reset`

### Test Results
**202 passed, 7 skipped** (was 163). 39 new tests across 5 new test files:
- `test_routing_history.py` — 8 tests
- `test_semantic_cache.py` — 14 tests
- `test_annealing.py` — 5 tests
- `test_config_reload.py` — 4 tests
- `test_integration.py` — 7 tests (skipped without MLX)
- `test_server.py` — 11 new endpoint tests added

### New Files
- `src/mlx_task_router/routing_history.py`
- `src/mlx_task_router/semantic_cache.py`
- `src/mlx_task_router/annealing.py`
- `tests/test_routing_history.py`
- `tests/test_semantic_cache.py`
- `tests/test_annealing.py`
- `tests/test_config_reload.py`
- `tests/test_integration.py`

### Modified Files
- `src/mlx_task_router/local.py` — real-time streaming, prompt KV cache
- `src/mlx_task_router/server.py` — 12 new endpoints, semantic cache integration, annealing lifecycle, version bump to 0.5.0
- `src/mlx_task_router/router.py` — annealing weight adjustments in `_score_forward()`
- `src/mlx_task_router/config.py` — `reload()` method
- `TODO.md` — 7 medium-priority items marked complete
- `CHANGELOG.md` — this entry

---

## [0.4.1] — 2025-07-25

### Improvements

#### Server-Level Tests (23 new tests)
Added comprehensive `test_server.py` using FastAPI `TestClient`. Covers:
- Non-streaming request/response format (Anthropic API contract)
- Streaming SSE event structure (message_start, message_stop)
- `@local` routing override
- Fail-open when model not loaded (forwards to Claude)
- Cache interaction (second identical request returns cached result)
- Cache clear endpoint
- Stats, perf, and cache stats endpoints
- CORS headers (allow-origin: *)
- Token count endpoint

**163/163 tests pass** (was 140).

#### Feedback I/O Performance Fix
Rewrote `feedback.py` to use a dirty flag + 30-second periodic flush thread (mirrors `stats.py` pattern). Previously wrote JSON to disk on **every single request**. Now batches writes, eliminating unnecessary disk I/O under load. Added `start()`/`stop()` lifecycle methods wired into server lifespan.

#### CORS Middleware
Added `CORSMiddleware` to `server.py` with fully open configuration:
- `allow_origins=["*"]` — any browser-based AI app can call the router
- `allow_methods=["*"]` — all HTTP methods
- `allow_headers=["*"]` — all headers including `x-api-key`, `anthropic-version`

Required for any web frontend that sends requests to the router from a browser.

#### Updated Pricing Table
Expanded `stats.py` pricing from 3 tiers to 7, covering the full Claude model family:
- **Claude 4**: opus_4, sonnet_4
- **Claude 3.5**: sonnet_3_5, haiku_3_5
- **Claude 3**: opus_3, sonnet_3, haiku_3

Rewrote `_detect_tier()` to correctly parse modern model strings like `claude-sonnet-4-20250514`, `claude-3-5-haiku-20241022`, etc. Default tier updated from `sonnet` to `sonnet_4`. Added 11 tier detection tests.

### Files Changed
- `src/mlx_task_router/server.py` — CORS middleware, feedback lifecycle
- `src/mlx_task_router/feedback.py` — batch writes with flush thread
- `src/mlx_task_router/stats.py` — expanded pricing, rewritten tier detection
- `tests/test_server.py` — new file, 23 server-level tests
- `tests/test_stats.py` — updated tier detection tests for all model families
- `TODO.md` — all 13 recommendations added, 4 high-priority items completed
- `INTEGRATION.md` — new file, comprehensive integration guide
- `README.md` — added "Works With Any AI Project" section with SDK examples

---

## [0.4.0] — 2025-07-25

### Project Context
MLX Task Router is a smart proxy that sits between Claude Code / Windsurf and the Anthropic API. It aggressively routes tasks to a local MLX model to save costs, forwarding only when the task clearly exceeds local capability. This release rewrites the routing philosophy from "opt-in to local" to "default local, opt-in to forward."

### Current Phase
**Reassessment** — routing philosophy inverted for aggressive local routing, model upgraded to Qwen3-32B, generation params fixed per Qwen3 best practices.

### Routing Philosophy Change — Default LOCAL

**Previous (v0.3.0):** Score started at 0.0; positive signals (executable, CLI phrases) pushed toward LOCAL; needed ≥0.5 to route locally.
**New (v0.4.0):** Forward score starts at 0.0; positive signals (complexity, code generation, extended conversation) push toward FORWARD; needs ≥0.5 to forward. Everything else stays LOCAL by default.

**Impact:** ~70-80% of requests now route locally (up from ~30-40%), significantly reducing API costs.

### Model Change — Qwen3-32B replaces Qwen2.5-Coder-32B

**Previous default:** `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` (dense 32B, Sept 2024)
**New default:** `mlx-community/Qwen3-32B-4bit` (dense 32B, April 2025)

**Rationale:** Qwen3-32B scores **75.7% on BFCL v3** (Berkeley Function Calling Leaderboard) — the #1 open-weight model for function calling accuracy. It's 7 months newer, has superior agent capabilities, hybrid thinking/non-thinking modes, and improved reasoning. Same parameter count and inference speed (~25-35 tok/s on M4 Max).

### Critical Fix — Generation Parameters

**Bug:** v0.3.0 used `MLX_TEMPERATURE=0.0` (greedy decoding).
**Problem:** Qwen3 official docs: "DO NOT use greedy decoding, as it can lead to performance degradation and endless repetitions."
**Fix:** Updated defaults to Qwen3 recommended: `temp=0.7, top_p=0.8, top_k=20`.

### Changes

#### Routing Logic Rewrite (`router.py`)
- **Philosophy inverted:** Default is LOCAL, not FORWARD
- **New forward signals:** complexity patterns (+0.5), code generation (+0.3), extended conversation >10 turns (+0.4), long message >500 chars (+0.2), question chain (+0.2)
- **New local reinforcement:** executable first word (-0.3), CLI action phrase (-0.3), short message (-0.1, only when no forward signals)
- **New hard forward: `thinking_requested`** — requests with `budget_tokens` always forward to Claude
- **New hard forward: context too large** (unchanged)
- **Extended conversation detection:** `_count_turns()` counts user messages in conversation
- **Short message gating:** `short` bonus only applies when no forward signals fired (prevents canceling out complexity)
- **Executable ignore list expanded:** Added `compare`, `security`, `plan`, `design`, `optimize`, `migrate` (macOS tools that conflict with complexity patterns)
- **Complexity pattern fix:** `debug/fix` pattern now allows adjective+noun (e.g., "debug this memory leak")

#### Model & Generation (`config.py`, `local.py`)
- Default model: `mlx-community/Qwen3-32B-4bit`
- Default temperature: `0.7` (was `0.0`)
- Default top_p: `0.8` (was `1.0`)
- New parameter: `MLX_TOP_K=20`
- `top_k` passed to both `generate()` and `stream_generate()`

#### Tests (all 140 pass)
- `test_router.py`: Rewritten for forward-scoring philosophy; added `TestScoreForward`, `TestCountTurns`, thinking request test, neutral message test
- `test_adaptive_routing.py`: Updated for inverted threshold semantics
- `test_config.py`: Updated for new model and generation param defaults
- `test_benchmark.py`: Added neutral category test; 52 fixtures across cli/complex/codegen/neutral/override categories
- `benchmark_requests.json`: Reorganized — added 8 neutral fixtures, moved codegen to expect LOCAL (aggressive routing lets local handle these)

### Benchmark Results

| Category | Count | Accuracy | Notes |
|----------|-------|----------|-------|
| CLI | 28 | 100% | All route local |
| Neutral | 8 | 100% | Ambiguous messages default local |
| Complex | 11 | 100% | Deep reasoning forwards to Claude |
| Codegen | 3 | 100% | Simple code gen stays local |
| Override | 2 | 100% | @cloud/@local always respected |
| **Overall** | **52** | **100%** | |

### Neural Engine Assessment
Investigated whether the M4 Max 16-core Neural Engine (ANE) should be leveraged. **Conclusion: GPU via MLX is the correct path.** The ANE is architecturally unsuited for LLM inference — 32MB SRAM cache cliff, ~2048-token context limit, max ~8B parameters, no public API, and CoreML adds 2-4x overhead. GPU is 2-5x faster for token generation. Future opportunity: train a small CoreML routing classifier to run on the ANE for zero-GPU-cost semantic routing decisions, and prepare for M5 GPU Neural Accelerators (3.3-4x faster TTFT via Metal 4).

### Documentation
- Added `INTEGRATION.md` — comprehensive guide for connecting any AI-powered project to the router, including SDK examples (Python, TypeScript, cURL), full API compatibility matrix, routing decision table, safety guarantees, hardware requirements, and cost impact analysis.

### Next Steps
- ANE routing classifier — learned semantic routing on idle Neural Engine hardware
- M5 GPU Neural Accelerator readiness — stay on latest MLX for automatic Metal 4 support
- Benchmark Qwen3-32B vs Qwen2.5-Coder-32B on real tool-calling tasks
- Real-time token streaming (Phase 2.2)
- Prompt KV cache (Phase 3.2)

---

## [0.3.0] — 2025-04-25

### Project Context
MLX Task Router is a smart proxy that sits between Claude Code / Windsurf and the Anthropic API. It routes simple CLI tasks to a local MLX model and forwards complex tasks upstream. This release is a full performance overhaul optimized for Apple M4 Max with 128GB unified memory.

### Current Phase
**Optimization** — maximizing local routing capability, inference speed, and observability.

### Model Change — Qwen2.5-Coder-32B replaces Qwen3-Coder-30B MoE

**Previous default:** `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` (MoE, ~3B active)
**New default:** `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` (dense 32B, ~19GB)

**Rationale:** The MoE model only activates 3B parameters per token — fast but weak tool-calling. The dense 32B model uses all parameters, giving significantly better function-call accuracy. On M4 Max with 546 GB/s bandwidth, inference runs at ~25-35 tok/s, adequate for CLI tasks where most responses are under 200 tokens. The 19GB footprint leaves 109GB free on a 128GB machine.

### Changes

#### Phase 1: Model Selection & Hardware Tuning

##### `src/mlx_task_router/config.py`
- **Changed** `DEFAULT_MODEL` to `Qwen2.5-Coder-32B-Instruct-4bit`
- **Added** `temperature` field (from `MLX_TEMPERATURE`, default: `0.0` — greedy/deterministic for tool calls)
- **Added** `top_p` field (from `MLX_TOP_P`, default: `1.0`)
- **Added** `repetition_penalty` field (from `MLX_REPETITION_PENALTY`, default: `1.05`)
- **Added** `draft_model` field (from `MLX_DRAFT_MODEL`, default: empty — disabled)
- **Added** `speculative_tokens` field (from `MLX_SPECULATIVE_TOKENS`, default: `5`)
- **Added** `adaptive_routing` field (from `ADAPTIVE_ROUTING`, default: `true`)

##### `src/mlx_task_router/local.py`
- **Added** `_warmup()` method — runs 1-token generation after load to prime Metal shader caches, eliminating first-request latency spike
- **Added** generation parameter passthrough — `temp`, `top_p`, `repetition_penalty` now passed to `mlx_generate()` and `mlx_stream()`
- **Added** draft model loading — when `MLX_DRAFT_MODEL` is configured, loads alongside main model for speculative decoding
- **Added** speculative decoding — passes `draft_model` and `num_draft_tokens` to `mlx_generate()` when draft model available (1.5-2.5x speedup)

#### Phase 2: Connection & I/O Performance

##### `src/mlx_task_router/proxy.py`
- **Replaced** per-request `httpx.AsyncClient` with persistent module-level client
- **Added** connection pooling: 20 max connections, 10 keepalive, 30s expiry
- **Added** `shutdown_client()` for clean server shutdown
- **Impact:** Eliminates ~50-100ms TCP+TLS handshake per forwarded request

##### `src/mlx_task_router/server.py`
- **Added** `shutdown_client()` call in lifespan shutdown
- **Added** request timing around routing and generation paths
- **Added** `_extract_tokens_from_events()` helper for SSE token extraction
- **Added** perf metric recording for local, forward, and cache-hit requests
- **Added** `GET /perf` endpoint returning latency percentiles, tokens/sec, counts

#### Phase 4: Smarter Routing

##### `src/mlx_task_router/router.py`
- **Added** `_adaptive_threshold()` — auto-calibrates routing threshold from feedback data. Below 10% failure rate → lower threshold (more local routing). Above 10% → raise threshold (more conservative). Clamped to [0.2, 0.8]. Requires 20+ attempts before activating.
- **Added** accurate token estimation — uses real tokenizer when model loaded, falls back to char//4 heuristic
- **Added** 6 new CLI action phrase patterns: cherry-pick/rebase/merge, lint/format/typecheck, deploy/publish/release, start/stop/restart, tail/grep/cat/head, kill process
- **Added** `model_manager` import for tokenizer-based estimation

#### Phase 5: Observability & Benchmarking

##### `src/mlx_task_router/perf.py` *(new)*
- **Added** `RequestMetric` dataclass — timestamp, route, total_ms, routing_ms, generation_ms, tokens, tokens_per_sec
- **Added** `PerfMetrics` class — thread-safe ring buffer (500 entries), percentile calculations, summary stats
- **Exposed** via `GET /perf` endpoint

##### `tests/test_perf.py` *(new)*
- 5 tests: metric properties, empty summary, record+summary, ring buffer eviction, mixed routes

##### `tests/test_adaptive_routing.py` *(new)*
- 7 tests: disabled mode, no feedback, insufficient data, low/high failure adaptation, clamping

##### `tests/test_benchmark.py` *(new)*
- Routing accuracy benchmark against 50 labeled fixtures
- Per-category accuracy assertions (CLI ≥80%, complex ≥80%, overrides = 100%)
- Overall accuracy threshold: ≥85%

##### `tests/fixtures/benchmark_requests.json` *(new)*
- 50 labeled test requests: 25 CLI (expect local), 20 complex (expect forward), 3 override, 2 edge cases

### New Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MLX_TEMPERATURE` | `0.0` | Sampling temperature (0 = greedy, best for tool calling) |
| `MLX_TOP_P` | `1.0` | Nucleus sampling threshold |
| `MLX_REPETITION_PENALTY` | `1.05` | Prevent degenerate repetition loops |
| `MLX_DRAFT_MODEL` | `""` | Draft model for speculative decoding (empty = disabled) |
| `MLX_SPECULATIVE_TOKENS` | `5` | Candidate tokens per speculative step |
| `ADAPTIVE_ROUTING` | `true` | Auto-calibrate routing threshold from feedback |

### New API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/perf` | Performance metrics — latency percentiles, tokens/sec, request counts |

### Test Results
- **130/130 tests pass** — 25 new tests added across 3 new test files

### Migration
No breaking changes. Existing `.env` files work unchanged. New features activate via new env vars.

To use the new default model:
```bash
# Update your config (or just delete MLX_MODEL to use new default)
MLX_MODEL=mlx-community/Qwen2.5-Coder-32B-Instruct-4bit

# Optional: enable speculative decoding for ~1.5-2.5x speedup
MLX_DRAFT_MODEL=mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit
```

---

## [0.2.0] — 2025-04-25

### Project Context
MLX Task Router is a smart proxy that sits between Claude Code / Windsurf and the Anthropic API. It routes simple CLI tasks to a local MLX model and forwards complex tasks upstream.

### Current Phase
**Simplification** — removing unused complexity to keep the codebase clean and maintainable.

### Breaking Change — Remove gear system

The "gear shifting" feature (eco/sport/track model profiles with runtime switching) has been removed. The gear system was never wired into the routing logic — it did not auto-switch models based on task complexity as the design implied. A single model loaded at startup was always used for all local requests, making multiple gear profiles dead code.

**What changed:**
- Model is now configured via `MLX_MODEL` env var (replaces `DEFAULT_GEAR`, `GEAR_ECO_MODEL`, `GEAR_SPORT_MODEL`, `GEAR_TRACK_MODEL`)
- Max generation tokens configured via `MLX_MAX_TOKENS` env var (default: 8192)
- CLI `--model` flag replaces `--gear` flag
- `gears` CLI subcommand removed

**Migration:** Replace gear config in `~/.config/mlx-task-router/.env`:
```diff
- DEFAULT_GEAR=sport
- GEAR_SPORT_MODEL=mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
+ MLX_MODEL=mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
+ MLX_MAX_TOKENS=8192
```

### Changes

#### `src/mlx_task_router/config.py`
- **Removed** `GearProfile` dataclass, `DEFAULT_GEARS` dict, `_build_gears()` function
- **Removed** `default_gear` and `gears` fields from `Config`
- **Added** `model_name` field (from `MLX_MODEL` env var, default: `Qwen3-Coder-30B-A3B-Instruct-4bit`)
- **Added** `model_max_tokens` field (from `MLX_MAX_TOKENS` env var, default: 8192)
- **Added** `DEFAULT_MODEL` and `DEFAULT_MAX_TOKENS` module constants
- Removed unused `field` import from `dataclasses`

#### `src/mlx_task_router/local.py`
- **Renamed** `load_gear(gear: GearProfile)` → `load_model(model_name: str)`
- **Renamed** `current_gear` property → `current_model` (returns `str | None`)
- **Removed** `GearProfile` import
- Max tokens now read from `config.model_max_tokens` instead of gear profile
- Log messages updated: `[gear]` → `[model]`

#### `src/mlx_task_router/server.py`
- **Removed** 3 gear management endpoints: `GET /gears`, `GET /gear`, `POST /gear/{name}`
- **Updated** lifespan to load `config.model_name` directly
- **Updated** routing log to show model name instead of gear name
- **Updated** `/health` and `/` endpoints to return `model` field instead of `gear`

#### `src/mlx_task_router/watchdog.py`
- **Updated** `_attempt_recovery` to call `load_model(model_name)` instead of `load_gear(gear)`

#### `src/mlx_task_router/cli.py`
- **Replaced** `--gear` CLI flag with `--model`
- **Removed** `gears` subcommand and `_list_gears()` function
- **Updated** startup banner to show model path instead of gear name
- **Updated** `init` config template to use `MLX_MODEL` instead of `DEFAULT_GEAR`

#### `.env.example`
- **Replaced** gear config section with `MLX_MODEL` and `MLX_MAX_TOKENS`

#### `tests/test_config.py`
- **Rewritten** — replaced 7 gear-related tests with 5 tests for `DEFAULT_MODEL`, `DEFAULT_MAX_TOKENS`, `Config` defaults, and env var overrides

#### `README.md`
- **Removed** entire "Gear Shifting" section (Built-in Gears, Switching Gears at Runtime, Custom Models)
- **Added** "Model Configuration" section with `MLX_MODEL` / `MLX_MAX_TOKENS` instructions
- **Removed** Gear Management API endpoints table
- **Updated** CLI Reference, Configuration Reference, Troubleshooting, Limitations, Project Structure
- **Removed** all gear references (~50 occurrences)

### Removed API Endpoints
| Method | Endpoint | Replacement |
|--------|----------|-------------|
| `GET` | `/gears` | Removed — no replacement needed |
| `GET` | `/gear` | Use `GET /health` (returns `model` field) |
| `POST` | `/gear/{name}` | Set `MLX_MODEL` env var and restart |

### Removed Environment Variables
| Variable | Replacement |
|----------|-------------|
| `DEFAULT_GEAR` | `MLX_MODEL` |
| `GEAR_ECO_MODEL` | `MLX_MODEL` |
| `GEAR_SPORT_MODEL` | `MLX_MODEL` |
| `GEAR_TRACK_MODEL` | `MLX_MODEL` |

### Test Results
- **105/105 tests pass** — 7 gear tests removed, 5 config tests added

---

## [0.1.1] — 2025-04-25

### Project Context
MLX Task Router is a smart proxy that sits between Claude Code / Windsurf and the Anthropic API. It routes simple CLI tasks to a local MLX model and forwards complex tasks upstream. This release fixes a critical bug that caused **all proxied messages to fail** after conversations accumulated extended-thinking history.

### Current Phase
**Stabilisation** — ensuring the proxy transparently handles the full Anthropic Messages API surface without blocking valid requests.

### Bug Fix — Proxy fails on unknown content block types

**Root cause:** The `ContentBlock` pydantic union in `models.py` only recognised five content types (`text`, `image`, `tool_use`, `tool_result`, `thinking`). Modern Anthropic API clients (Windsurf / Claude Code with extended thinking enabled) include additional types such as `redacted_thinking` in conversation history. Pydantic validation rejected these, returning a **400 error before routing even happened** — blocking both LOCAL and FORWARD paths for every request containing unknown content types.

**Symptoms:**
- All proxied messages fail after the first few turns of a conversation
- Server logs show pydantic `ValidationError` on `/v1/messages`
- Both streaming and non-streaming requests affected

### Changes

#### `src/mlx_task_router/models.py`
- **Added `ContentBlockRedactedThinking`** — explicit model for `redacted_thinking` blocks (extended thinking history)
- **Added `ContentBlockGeneric`** — catch-all model with `extra="allow"` for any unknown/future content block types (`document`, `server_tool_use`, `citations`, etc.)
- **Updated `ContentBlock` union** — now includes `ContentBlockRedactedThinking` and `ContentBlockGeneric` at the end of the union so known types match first, unknown types fall through to the generic
- Imported `ConfigDict` from pydantic for the catch-all model

#### `src/mlx_task_router/server.py`
- **Graceful fallback on parse failure** — when `MessagesRequest(**body)` raises a validation error, the handler now logs the error and forwards the raw body directly to the Anthropic API instead of returning HTTP 400. This ensures the proxy never blocks valid API requests due to schema drift.
- Streaming and non-streaming fallback paths both implemented
- Stats tracking preserved in the fallback path

#### `src/mlx_task_router/proxy.py`
- **Added upstream logging** — `forward_request` now logs the target URL and response status (`200 OK` or error status + first 200 chars of body)
- **Added stream error handling** — `stream_forward` now checks for non-200 upstream responses, reads the error body, logs it, and yields it as a single chunk instead of silently streaming garbled error data
- **Added stream completion logging** — logs when a stream successfully completes

#### `tests/test_models.py` *(new)*
- 11 regression tests covering:
  - All known content block types (`text`, `thinking`, `redacted_thinking`, `tool_use`, `tool_result`)
  - Unknown/future types falling through to `ContentBlockGeneric` (`server_tool_use`, `document`, `citations`)
  - Full `MessagesRequest` parsing with mixed content including `redacted_thinking` history
  - Extra top-level fields (`service_tier`, future fields) being silently ignored

### Test Results
- **108/108 tests pass** (all existing + 11 new)

### Next Steps
- Monitor logs for `[parse] Validation failed` entries to identify additional content types that should get explicit models
- Consider adding explicit models for `document`, `server_tool_use`, `server_tool_result` if they become common
- Evaluate adding response status passthrough for streaming error responses (currently returns HTTP 200 with error body)
