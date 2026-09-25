"""Text -> vector. HashEmbedder needs no API key; swap in a real model via get_embedder()."""
import hashlib
import math
import re
from typing import Protocol

_WORD = re.compile(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])|\d+")
_STOP = {"self", "def", "class", "return", "the", "a", "an", "and", "or", "if", "else",
         "in", "is", "not", "none", "to", "of", "for", "import", "from", "with", "as"}


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    """Split code/text into lowercase words: is_eligible_for_refund / isEligible -> is, eligible, ..."""
    words = [w.lower() for w in _WORD.findall(text)]
    return [w for w in words if w not in _STOP and len(w) > 1]


class HashEmbedder:
    """Bag-of-words hashed into a fixed-size vector. Deterministic and fast; good for dev/tests."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _bucket(self, token: str) -> int:
        return int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in tokenize(text):
                vec[self._bucket(tok)] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


def get_embedder() -> Embedder:
    """Single place to switch to a real embedding model (OpenAI, Bedrock, ...) later."""
    return HashEmbedder()
