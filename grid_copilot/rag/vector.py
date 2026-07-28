"""Embedding-based retrieval over the domain corpus.

The keyword retriever matches on surface tokens, so a query built from opaque tag
names (``P1_PCV01D``) misses documents that describe the same thing in words
(a "pressure control valve on the boiler process"). This retriever embeds both
the query and every document with a sentence-transformers model and ranks by
cosine similarity, so it retrieves on meaning rather than shared words.

It also applies a similarity floor: below `min_score`, nothing is returned. That
fixes a real defect seen on live HAI runs, where the keyword retriever always
handed back its top-k even for an unrelated query, so the agent cited a
cavitation note for a boiler pressure valve. With a floor, an off-topic query
returns no evidence and the agent says so, instead of grounding on a false match.

sentence-transformers is an optional dependency (it pulls a torch + model stack),
imported lazily and reused from the Mnemos install when present. The class
implements the same `Retriever` protocol as `KeywordRetriever`, so it drops into
the tools, CLI, and eval without any other change.
"""

from __future__ import annotations

from grid_copilot.rag.corpus import CORPUS, Doc


class VectorRetriever:
    def __init__(
        self,
        docs: list[Doc] | None = None,
        model_name: str = "all-MiniLM-L6-v2",
        min_score: float = 0.25,
    ) -> None:
        from sentence_transformers import SentenceTransformer  # lazy, optional

        self.docs = docs if docs is not None else CORPUS
        self.min_score = min_score
        self._model = SentenceTransformer(model_name)
        # Normalized embeddings, so a dot product is cosine similarity.
        self._doc_vecs = self._model.encode(
            [f"{d.title}. {d.text}" for d in self.docs],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def search(self, query: str, k: int = 3) -> list[tuple[Doc, float]]:
        q = self._model.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0]
        scores = self._doc_vecs @ q  # cosine similarity per doc
        ranked = sorted(zip(self.docs, scores), key=lambda x: x[1], reverse=True)
        return [(doc, float(s)) for doc, s in ranked[:k] if s >= self.min_score]
