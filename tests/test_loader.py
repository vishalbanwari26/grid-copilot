"""Document loader tests: real spec/manual text is chunked into retrievable Docs."""

from __future__ import annotations

from grid_copilot.rag.corpus import CORPUS
from grid_copilot.rag.loader import load_documents
from grid_copilot.rag.retriever import KeywordRetriever


def test_loader_chunks_long_text(tmp_path):
    f = tmp_path / "modbus.txt"
    f.write_text(
        "Registers hold sixteen-bit values.\n\n"
        + "Exception responses set the high bit of the function code. " * 60
        + "\n\nCoils are single bits."
    )
    docs = load_documents(f)
    assert len(docs) >= 2  # long body split into multiple chunks
    assert all(d.id.startswith("MODBUS-") for d in docs)
    assert all(d.text for d in docs)


def test_retriever_searches_loaded_docs(tmp_path):
    f = tmp_path / "modbus.txt"
    f.write_text(
        "A Modbus exception response returns the function code with its high bit set, "
        "plus an exception code that tells the client why the request failed."
    )
    corpus = list(CORPUS) + load_documents(f)
    hits = KeywordRetriever(docs=corpus).search("modbus exception response function code", k=3)
    assert any(doc.id.startswith("MODBUS-") for doc, _ in hits)


def test_loader_reads_directory(tmp_path):
    (tmp_path / "a.txt").write_text("First spec about holding registers.")
    (tmp_path / "b.md").write_text("Second spec about discrete inputs.")
    (tmp_path / "ignore.pdf").write_text("binary-ish, not text-loaded")
    docs = load_documents(tmp_path)
    ids = {d.id.split("-")[0] for d in docs}
    assert ids == {"A", "B"}  # .pdf skipped, both text files loaded
