"""Embedding-based semantic routing — augments regex heuristics with vector similarity.

Uses the local model's embedding layer to extract a dense representation of the
request, then classifies difficulty via a lightweight linear probe. Falls back
gracefully when the model is not loaded or insufficient training data exists.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from mlx_task_router.config import CONFIG_DIR, config

_PROBE_FILE = CONFIG_DIR / "embed_probe.json"
_TRAINING_FILE = CONFIG_DIR / "embed_training.jsonl"


class EmbedRouter:
    """Lightweight embedding classifier for routing difficulty scoring."""

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._model_hash: str = ""
        self._probe_weights: list[float] | None = None
        self._probe_bias: float = 0.0
        self._ready = False
        self._lock = threading.Lock()
        self._load_probe()

    def attach_model(self, model: Any, tokenizer: Any, model_name: str) -> None:
        """Attach the loaded MLX model for embedding extraction."""
        self._model = model
        self._tokenizer = tokenizer
        self._model_hash = hashlib.sha256(model_name.encode()).hexdigest()[:12]
        self._load_probe()
        if self._ready:
            print(f"[embed] Probe loaded for model {model_name}")
        else:
            print(f"[embed] No trained probe yet — will activate after {config.embed_min_samples} samples")

    def detach_model(self) -> None:
        """Detach model reference on unload."""
        self._model = None
        self._tokenizer = None
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and self._model is not None

    def embed(self, text: str) -> list[float] | None:
        """Get mean-pooled embedding from model's embedding layer.

        Returns None if model is not available.
        """
        if self._model is None or self._tokenizer is None:
            return None
        try:
            import mlx.core as mx

            tokens = self._tokenizer.encode(text)
            if not tokens:
                return None
            # Truncate to avoid OOM on very long inputs
            tokens = tokens[:512]
            token_ids = mx.array([tokens])
            # Extract embeddings from the model's embedding layer (fast, ~2ms)
            embed_layer = getattr(self._model.model, "embed_tokens", None)
            if embed_layer is None:
                return None
            x = embed_layer(token_ids)
            # Mean pool across token dimension
            pooled = mx.mean(x, axis=1).squeeze()
            mx.eval(pooled)
            return pooled.tolist()
        except Exception as e:
            print(f"[embed] Embedding extraction failed: {e}")
            return None

    def score(self, text: str) -> float | None:
        """Return difficulty score [0, 1] or None if not ready.

        Higher score = more likely to need forwarding.
        """
        if not self.is_ready or not config.embed_routing:
            return None
        embedding = self.embed(text)
        if embedding is None:
            return None
        if self._probe_weights is None or len(embedding) != len(self._probe_weights):
            return None
        # Linear probe: sigmoid(embedding @ weights + bias)
        dot = sum(e * w for e, w in zip(embedding, self._probe_weights))
        logit = dot + self._probe_bias
        # Sigmoid
        import math
        try:
            score = 1.0 / (1.0 + math.exp(-logit))
        except OverflowError:
            score = 0.0 if logit < 0 else 1.0
        return score

    def record_example(self, text: str, should_forward: bool) -> None:
        """Record a training example for future probe retraining."""
        try:
            _TRAINING_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_TRAINING_FILE, "a") as f:
                f.write(json.dumps({
                    "text": text[:500],
                    "forward": should_forward,
                    "model_hash": self._model_hash,
                    "timestamp": time.time(),
                }) + "\n")
        except OSError:
            pass

    def training_sample_count(self) -> int:
        """Count available training examples for current model."""
        if not _TRAINING_FILE.exists():
            return 0
        count = 0
        try:
            with open(_TRAINING_FILE) as f:
                for line in f:
                    try:
                        row = json.loads(line.strip())
                        if row.get("model_hash") == self._model_hash:
                            count += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return count

    def train(self) -> bool:
        """Train linear probe from recorded examples.

        Returns True if training succeeded.
        Uses simple online logistic regression (gradient descent).
        """
        if self._model is None:
            return False

        examples = self._load_training_examples()
        if len(examples) < config.embed_min_samples:
            return False

        print(f"[embed] Training probe from {len(examples)} examples...")
        t0 = time.time()

        # Collect embeddings
        embeddings: list[list[float]] = []
        labels: list[float] = []
        for text, should_forward in examples:
            emb = self.embed(text)
            if emb is None:
                continue
            embeddings.append(emb)
            labels.append(1.0 if should_forward else 0.0)

        if len(embeddings) < 20:
            print(f"[embed] Only {len(embeddings)} valid embeddings — insufficient")
            return False

        dim = len(embeddings[0])

        # Initialize weights
        import math
        weights = [0.0] * dim
        bias = 0.0
        lr = 0.01

        # Gradient descent — 100 epochs over data
        for epoch in range(100):
            total_loss = 0.0
            for emb, label in zip(embeddings, labels):
                # Forward
                dot = sum(e * w for e, w in zip(emb, weights)) + bias
                try:
                    pred = 1.0 / (1.0 + math.exp(-dot))
                except OverflowError:
                    pred = 0.0 if dot < 0 else 1.0

                # Binary cross-entropy loss
                eps = 1e-7
                total_loss += -(label * math.log(pred + eps) + (1 - label) * math.log(1 - pred + eps))

                # Gradients
                error = pred - label
                for i in range(dim):
                    weights[i] -= lr * error * emb[i]
                bias -= lr * error

        avg_loss = total_loss / len(embeddings)
        elapsed = time.time() - t0
        print(f"[embed] Probe trained in {elapsed:.1f}s — loss={avg_loss:.4f}, dim={dim}")

        with self._lock:
            self._probe_weights = weights
            self._probe_bias = bias
            self._ready = True

        self._save_probe()
        return True

    def _load_training_examples(self) -> list[tuple[str, bool]]:
        """Load training examples for current model."""
        examples = []
        if not _TRAINING_FILE.exists():
            return examples
        try:
            with open(_TRAINING_FILE) as f:
                for line in f:
                    try:
                        row = json.loads(line.strip())
                        if row.get("model_hash") == self._model_hash:
                            examples.append((row["text"], row["forward"]))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass
        return examples

    def _load_probe(self) -> None:
        """Load trained probe weights if available and matching current model."""
        if not _PROBE_FILE.exists():
            return
        try:
            data = json.loads(_PROBE_FILE.read_text())
            if data.get("model_hash") != self._model_hash:
                print(f"[embed] Probe model mismatch — ignoring stale probe")
                return
            self._probe_weights = data["weights"]
            self._probe_bias = data["bias"]
            self._ready = True
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"[embed] Failed to load probe: {e}")

    def _save_probe(self) -> None:
        """Persist probe weights to disk."""
        try:
            _PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PROBE_FILE.write_text(json.dumps({
                "model_hash": self._model_hash,
                "weights": self._probe_weights,
                "bias": self._probe_bias,
                "dim": len(self._probe_weights) if self._probe_weights else 0,
                "trained_at": time.time(),
            }))
        except OSError as e:
            print(f"[embed] Failed to save probe: {e}")

    def status(self) -> dict[str, Any]:
        """Status summary for the /embed endpoint."""
        return {
            "ready": self.is_ready,
            "enabled": config.embed_routing,
            "model_attached": self._model is not None,
            "model_hash": self._model_hash,
            "probe_loaded": self._probe_weights is not None,
            "probe_dim": len(self._probe_weights) if self._probe_weights else 0,
            "training_samples": self.training_sample_count(),
            "min_samples": config.embed_min_samples,
            "weight": config.embed_weight,
        }


embed_router = EmbedRouter()
