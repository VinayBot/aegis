"""bge-small (384-d) through fastembed, in-process on the CPU.

Deliberately not on a model server: if MLX or Ollama crashes, the semantic cache keeps working.
Calls run in a worker thread so ONNX inference never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any

import numpy as np

DIM = 384


BATCH = 16  # measured: 300 chunks peak at 444 MB instead of 1.49 GB with the default 256


class Embedder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        from fastembed import TextEmbedding

        with self._lock:
            if self._model is None:
                self._model = TextEmbedding(self.model_name, threads=2)

    def embed_sync(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            self.load()
        assert self._model is not None
        start = time.perf_counter()
        with self._lock:  # one ONNX session; serialise access
            # small batches: fastembed's default (256) makes ONNX allocate activations for the
            # whole batch at once, a ~1.5 GB peak when the docs index is built at startup
            vectors = np.asarray(list(self._model.embed(texts, batch_size=BATCH)), dtype=np.float32)
        self.latencies_ms.append((time.perf_counter() - start) * 1000)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        normalised: np.ndarray = vectors / np.maximum(norms, 1e-9)
        return normalised

    async def embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embed_sync, texts)

    async def embed_one(self, text: str) -> np.ndarray:
        vector: np.ndarray = (await self.embed([text]))[0]
        return vector
