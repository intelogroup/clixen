"""Shared test fixtures: temp dirs, stub embedder, module patch helpers."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

SCRIPTS = Path("/root/pi-agent/scripts")

for p in (SCRIPTS,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class StubEmbedder:
    """Deterministic, no-network embedder. 384-dim (matches lance schema)."""

    DIM = 384

    def encode(self, texts, normalize_embeddings=True, **kw):
        if isinstance(texts, str):
            texts = [texts]
        out = np.zeros((len(texts), self.DIM), dtype="float32")
        for i, t in enumerate(texts):
            h = hashlib.sha256(t.encode()).digest()
            for j in range(self.DIM):
                out[i, j] = h[j % len(h)] / 255.0 - 0.5
        if normalize_embeddings:
            out = out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)
        return out


class TrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pi-test-")

    def tmp_path(self, name="tracker.db"):
        return str(Path(self.tmp) / name)

    def patch_module_paths(self, mod, *, db=None, lance=None, out=None):
        if db is not None:
            mod.DB_PATH = Path(db)
        if lance is not None:
            mod.LANCEDB_DIR = Path(lance)
        if out is not None:
            mod.OUT_DIR = Path(out)

    def patch_embedder(self, mod):
        mod.get_embedder = lambda: StubEmbedder()