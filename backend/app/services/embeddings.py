import hashlib
import json
import math
import os
import re
from functools import lru_cache
from typing import Iterable

import numpy as np


DIMENSION = 384


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())


@lru_cache(maxsize=1)
def _model():
    try:
      from sentence_transformers import SentenceTransformer
      allow_download = os.getenv("READSYNC_ALLOW_MODEL_DOWNLOAD", "0") == "1"
      return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", local_files_only=not allow_download)
    except Exception:
      return None


def _hash_embedding(text: str) -> list[float]:
    vector = np.zeros(DIMENSION, dtype=np.float32)
    tokens = tokenize(text)
    if not tokens:
        tokens = ["empty"]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % DIMENSION
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[idx] += sign * (1.0 + math.log1p(tokens.count(token)))
    norm = np.linalg.norm(vector)
    if norm:
        vector = vector / norm
    return vector.astype(float).tolist()


def embed_text(text: str) -> list[float]:
    model = _model()
    if model is not None:
        try:
            vector = model.encode([(text or "")[:8000]], normalize_embeddings=True)[0]
            return [float(x) for x in vector.tolist()]
        except Exception:
            pass
    return _hash_embedding(text or "")


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    va = np.array(list(a), dtype=np.float32)
    vb = np.array(list(b), dtype=np.float32)
    if va.size == 0 or vb.size == 0:
        return 0.0
    size = min(va.size, vb.size)
    va = va[:size]
    vb = vb[:size]
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(va, vb) / denom)))


def embedding_to_json(vector: list[float]) -> str:
    return json.dumps([round(float(x), 6) for x in vector])


def embedding_from_json(value: str | None) -> list[float]:
    try:
        return [float(x) for x in json.loads(value or "[]")]
    except Exception:
        return []


def keyword_fingerprint(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "into", "using",
        "return", "class", "def", "function", "const", "let", "var",
    }
    return {token for token in tokenize(text) if token not in stop}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
