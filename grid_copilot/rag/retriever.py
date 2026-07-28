"""Retrieval over the domain corpus.

The offline default is a dependency-free keyword retriever: it scores documents
by overlap between the query terms and the document text, with a mild boost for
rarer terms so a distinctive word like "cavitation" outweighs a common one like
"pressure". That is enough to ground the agent's reasoning in the right note
without pulling in an embedding model.

The `Retriever` protocol is the seam: a vector retriever (sentence-transformers
plus a store) implements the same `search` and drops in for real documents,
exactly as the memory layer swaps a local store for Mnemos.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

from grid_copilot.rag.corpus import CORPUS, Doc

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class Retriever(Protocol):
    def search(self, query: str, k: int = 3) -> list[tuple[Doc, float]]:
        ...


class KeywordRetriever:
    """TF-IDF-ish keyword scorer over a fixed corpus."""

    def __init__(self, docs: list[Doc] | None = None) -> None:
        self.docs = docs if docs is not None else CORPUS
        # Inverse document frequency, so rare terms carry more weight.
        n = len(self.docs)
        df: Counter[str] = Counter()
        self._doc_tokens: list[Counter[str]] = []
        for doc in self.docs:
            toks = Counter(_tokens(doc.title + " " + doc.text))
            self._doc_tokens.append(toks)
            for term in toks:
                df[term] += 1
        self._idf = {term: math.log((n + 1) / (c + 1)) + 1.0 for term, c in df.items()}

    def search(self, query: str, k: int = 3) -> list[tuple[Doc, float]]:
        q_terms = set(_tokens(query))
        scored: list[tuple[Doc, float]] = []
        for doc, toks in zip(self.docs, self._doc_tokens):
            score = sum(toks[t] * self._idf.get(t, 1.0) for t in q_terms)
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
