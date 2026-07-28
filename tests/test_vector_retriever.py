"""Vector retriever test. Skips if sentence-transformers is not installed.

Confirms embedding retrieval returns the domain-relevant notes for a query about
a real HAI tag, and that the similarity floor rejects an off-topic query (so the
agent is not handed a false match to ground on).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sentence_transformers", reason="sentence-transformers not installed")

from grid_copilot.rag.vector import VectorRetriever  # noqa: E402


def test_vector_retrieves_relevant_docs():
    vec = VectorRetriever()
    hits = vec.search("a pressure control valve command dropped on the boiler process", k=3)
    assert hits, "expected at least one relevant document"
    ids = {doc.id for doc, _ in hits}
    assert ids & {"KB-ISA-TAGS", "KB-CONTROL-VALVE", "KB-HAI-PROCESSES"}


def test_vector_rejects_offtopic_query():
    vec = VectorRetriever()
    assert vec.search("the quarterly sales report was late again", k=3) == []
