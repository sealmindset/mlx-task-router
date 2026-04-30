# Implementation Spec: Multi-Model Pool (3B/27B/Opus)

## Status: PROPOSED
## Priority: High
## Estimated Effort: 1 day

---

## Problem

Currently all local requests go to a single 27B model. But ~60% of requests are
trivial CLI operations (git status, ls, simple edits) that don't need 27B capacity.
These could be served by a tiny 1.5-3B model at 5-10x the speed.

Conversely, some requests that route locally would benefit from the 27B model's full
attention, while truly hard tasks should still go to Opus.

The single-model approach wastes capacity on trivial tasks and forces a binary
local-vs-forward decision when a three-tier approach would be more efficient.

## Solution

Load multiple models simultaneously and route requests to the optimal tier:

```
  Tier 0: Qwen2.5-Coder-1.5B  (~1GB VRAM)  — CLI, simple edits, git ops
  Tier 1: Qwen3.6-27B          (~18GB VRAM) — coding, refactoring, analysis
  Tier 2: Claude Opus 4.6      (API)        — multi-signal complex tasks
```

Total VRAM: ~19GB (fits easily in 128GB M4 Max).

## Architecture

```
                    ┌──────────────────┐
  request ─────────▶│  classify()      │
                    │  (router.py)     │
                    └───┬──────┬───┬───┘
                        │      │   │
              tier=0    │      │   │  tier=2
         ┌──────────────┘      │   └──────────────┐
         ▼                     ▼                   ▼
  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
  │ Qwen 1.5B    │   │ Qwen 27B     │   │ Claude Opus  │
  │ (fast tier)  │   │ (main tier)  │   │ (API tier)   │
  └──────────────┘   └──────────────┘   └──────────────┘
```

### Routing Logic

Extend `classify()` to return a tier instead of binary local/forward:

```python
class Route:
    FAST = "fast"      # Tier 0: 1.5B model
    LOCAL = "local"    # Tier 1: 27B model (default)
    FORWARD = "forward"  # Tier 2: Opus API

def classify(request) -> tuple[str, str, list[str]]:
    # Hard forwards → FORWARD (unchanged)
    # score < 0.3 AND is_trivial(text) → FAST
    # score < 0.7 → LOCAL
    # score >= 0.7 → FORWARD
```

### Trivial Request Detection (Tier 0 signals)

```python
_TRIVIAL_PATTERNS = [
    r"^\s*(git|ls|cd|cat|head|tail|grep|find|mkdir|rm|cp|mv|pwd)\b",
    r"^\s*(npm|yarn|pip|cargo|go)\s+(install|run|build|test)\b",
    r"^(fix|add|remove|rename|delete|update)\s+(the\s+)?(typo|import|comment|variable|line)\b",
    r"^(show|list|check|view)\s+(me\s+)?(the\s+)?(file|status|log|diff|branch)\b",
]
_TRIVIAL_MAX_LEN = 100  # Messages > 100 chars are not trivial
```

### New File: `src/mlx_task_router/model_pool.py`

```python
class ModelPool:
    """Manages multiple loaded models with tier-based routing."""

    def __init__(self):
        self._models: dict[str, tuple[Any, Any]] = {}  # name → (model, tokenizer)
        self._samplers: dict[str, tuple[Any, list]] = {}
        self._prompt_caches: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return "main" in self._models

    @property
    def fast_available(self) -> bool:
        return "fast" in self._models

    def load_all(self) -> None:
        """Load main model and optionally fast model."""
        self._load_model("main", config.model_name)
        if config.fast_model:
            self._load_model("fast", config.fast_model)

    def generate(self, request, tier: str = "local") -> dict:
        """Generate with the appropriate model for the tier."""
        model_key = "fast" if tier == "fast" and self.fast_available else "main"
        model, tokenizer = self._models[model_key]
        # ... generate with appropriate model

    def stream_generate(self, request, tier: str = "local") -> Generator:
        """Stream generate with the appropriate model."""
        model_key = "fast" if tier == "fast" and self.fast_available else "main"
        # ... stream with appropriate model
```

## Config

| Variable | Default | Description |
|----------|---------|-------------|
| `MLX_FAST_MODEL` | `""` | Small fast model for trivial tasks. Empty = disabled. |
| `FAST_MODEL_MAX_TOKENS` | `2048` | Max tokens for fast model responses |
| `TRIVIAL_THRESHOLD` | `0.3` | Forward score below this → fast tier |

## Migration Path

1. **Phase 1** (this spec): Add ModelPool, wire tier routing, keep backward compat
   - If `MLX_FAST_MODEL` is empty, all local routes go to main model (current behavior)
   - If set, trivial requests route to fast model

2. **Phase 2** (future): Add model health monitoring per tier
   - If fast model crashes, fall back to main model (not Opus)
   - Per-tier perf metrics in dashboard

3. **Phase 3** (future): Dynamic model loading/unloading
   - Load fast model only during high-traffic periods
   - Unload to free VRAM when idle

## Integration Points

### server.py
```python
# In _handle_local(), pass tier to model_pool
if route == Route.FAST:
    return await _handle_local(parsed, body, request, tier="fast", ...)
```

### router.py — `classify()`
```python
# After existing scoring, check for trivial tier
if score < TRIVIAL_THRESHOLD and _is_trivial(text):
    return Route.FAST, "trivial", reasons
```

### dashboard.py
- Add "Fast" slice to the routing doughnut chart
- Add fast model tok/s to performance panel

### stats.py
- Add `record_fast()` method alongside `record_local()`
- Track fast model token usage separately

## Testing Strategy

1. **Unit tests** — `test_model_pool.py`
   - `test_load_main_only` — works without fast model
   - `test_load_main_and_fast` — both models load
   - `test_tier_selection` — correct model used per tier
   - `test_fast_fallback` — falls back to main if fast unavailable

2. **Router tests** — extend `test_router.py`
   - `test_trivial_routes_fast` — git status → FAST
   - `test_non_trivial_routes_local` — complex query → LOCAL
   - `test_fast_disabled_routes_local` — no fast model → LOCAL

3. **Benchmark** — extend fixtures
   - Add `trivial` category with 10+ CLI/simple fixtures
   - Verify ≥90% accuracy on trivial detection

## Risks & Mitigations

- **VRAM** — 1.5B model adds ~1GB. Negligible on 128GB machine.
- **Cold start** — loading two models takes ~15s instead of ~10s. Acceptable for
  a service that runs continuously.
- **Quality cliff** — 1.5B may produce poor output for edge cases. Mitigated by
  conservative trivial detection (short messages, explicit CLI patterns only).
- **Complexity** — two code paths for generation. Mitigated by ModelPool abstraction
  that presents a uniform interface.

## Dependencies

- No new packages. Uses existing mlx-lm load/generate.
- Requires a suitable 1.5B model on HuggingFace (already available:
  `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit`).
