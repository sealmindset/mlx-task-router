# Implementation Spec: Embedding-Based Semantic Routing

## Status: PROPOSED
## Priority: High
## Estimated Effort: 8 hours

---

## Problem

The current routing logic uses regex pattern matching to detect complexity, codegen,
and CLI signals. This is brittle:

- "Help me think through this architecture" → no pattern match (misses complexity)
- "What's the best way to handle auth?" → matches `what's` but intent is simple Q&A
- "Refactor this to use dependency injection across all 15 modules" → single complexity
  signal, but the scope makes it genuinely hard for local

Regex can't capture semantic intent, scope, or difficulty. We're leaving accuracy on
the table.

## Solution

Replace (or augment) regex heuristics with a lightweight embedding-based classifier
that scores request difficulty using vector representations from the local model's
own embedding layer.

## Architecture

```
                          ┌─────────────────────┐
  user message ──────────▶│  _embed_text()       │──── 768-dim vector
                          │  (MLX model layer 0) │
                          └─────────┬───────────┘
                                    │
                          ┌─────────▼───────────┐
                          │  _classify_embed()   │──── difficulty score [0, 1]
                          │  (linear probe)      │
                          └─────────┬───────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │  _score_forward() merges:     │
                    │  embed_score * EMBED_WEIGHT    │
                    │  + existing regex signals      │
                    │  + annealing adjustments       │
                    └───────────────────────────────┘
```

### Key Design Decisions

1. **Use the local model's embeddings** — no additional model download. Extract from
   the first transformer layer's output (mean-pooled across tokens). ~2ms latency.

2. **Linear probe classifier** — a single-layer linear model (768 → 1) trained on
   feedback data. Stored as a small .safetensors file (~6KB). Falls back to regex
   when insufficient training data.

3. **Hybrid scoring** — embedding score is added as another signal alongside existing
   regex signals, not a replacement. Weight: `EMBED_WEIGHT = 0.3` (configurable).

4. **Online learning** — feedback data (success/failure per trigger) is used to
   periodically retrain the probe. The annealing thread handles this.

5. **Cold start** — until 100+ labeled examples exist, embedding routing is disabled.
   Regex-only routing continues to work.

## New File: `src/mlx_task_router/embed_router.py`

```python
class EmbedRouter:
    def __init__(self, model, tokenizer):
        self._model = model
        self._tokenizer = tokenizer
        self._probe_weights = None  # mx.array, shape (hidden_dim, 1)
        self._probe_bias = None     # mx.array, shape (1,)
        self._ready = False
        self._load_probe()

    def embed(self, text: str) -> mx.array:
        """Get mean-pooled embedding from model's first layer."""
        tokens = self._tokenizer.encode(text)
        # Forward through embedding layer only (fast, ~2ms)
        x = self._model.model.embed_tokens(mx.array([tokens]))
        return mx.mean(x, axis=1).squeeze()

    def score(self, text: str) -> float | None:
        """Return difficulty score [0, 1] or None if not ready."""
        if not self._ready:
            return None
        embedding = self.embed(text)
        logit = embedding @ self._probe_weights + self._probe_bias
        return float(mx.sigmoid(logit).item())

    def train(self, examples: list[tuple[str, bool]]) -> None:
        """Train linear probe from (text, should_forward) pairs."""
        # Collect embeddings
        # Train with simple logistic regression (gradient descent, 100 steps)
        # Save to ~/.config/mlx-task-router/embed_probe.safetensors

    def _load_probe(self) -> None:
        """Load trained probe weights if available."""
        probe_path = CONFIG_DIR / "embed_probe.safetensors"
        if probe_path.exists():
            # Load weights, set self._ready = True
```

## Integration Points

### router.py — `_score_forward()`
```python
embed_score = embed_router.score(text)
if embed_score is not None:
    w = _get_weight(EMBED_WEIGHT, "embed")
    score += w * embed_score
    reasons.append(f"embed:{embed_score:.2f} +{w * embed_score:.2f}")
```

### annealing.py — `_anneal_step()`
```python
# After weight adjustment, check if enough feedback for probe retraining
if total_attempts >= 100 and total_attempts % 50 == 0:
    embed_router.train(feedback_examples)
```

### local.py — `ModelManager.load_model()`
```python
self._embed_router = EmbedRouter(self._model, self._tokenizer)
```

## Config

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBED_ROUTING` | `true` | Enable embedding-based routing signals |
| `EMBED_WEIGHT` | `0.3` | Weight of embedding score in forward scoring |
| `EMBED_MIN_SAMPLES` | `100` | Minimum feedback samples before probe training |

## Testing Strategy

1. **Unit tests** — `test_embed_router.py`
   - `test_embed_returns_vector` — verify embedding shape
   - `test_score_none_when_untrained` — cold start returns None
   - `test_score_range` — trained probe returns [0, 1]
   - `test_train_from_examples` — probe trains without error
   - `test_probe_persistence` — save/load round-trip

2. **Integration tests** — add to `test_router.py`
   - `test_embed_signal_added_to_score` — verify hybrid scoring
   - `test_embed_disabled_by_config` — config toggle works

3. **Benchmark** — extend `test_benchmark.py`
   - Compare regex-only vs hybrid accuracy on existing fixtures

## Risks & Mitigations

- **Latency** — embedding extraction adds ~2ms. Acceptable since routing currently
  takes <1ms and total request latency is 500-5000ms.
- **Model coupling** — probe trained on one model won't work with another. Probe is
  invalidated on model change (check model name hash).
- **Feedback quality** — success/failure feedback may not perfectly correlate with
  routing quality. Mitigated by requiring 100+ samples.

## Dependencies

- `mlx` (already installed)
- No new packages required
