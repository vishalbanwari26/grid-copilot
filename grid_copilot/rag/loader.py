"""Load real documentation (protocol specs, equipment manuals) into the corpus.

The built-in corpus is a handful of hand-written notes; real grounding wants real
documents. This loader reads plain-text or markdown files and chunks them into
`Doc`s that the retriever searches alongside the notes, so the agent can cite
actual documentation. It ships the loader, not the documents: point ``--docs`` at
a file or directory (convert a PDF spec with ``pdftotext spec.pdf spec.txt``
first), so copyrighted specs are used locally without being redistributed here.

Chunking is paragraph-aware: it packs whole paragraphs up to a target size so a
retrieved chunk is a coherent passage rather than a mid-sentence fragment, and
each chunk keeps a stable id derived from the filename so citations are traceable.
"""

from __future__ import annotations

import re
from pathlib import Path

from grid_copilot.rag.corpus import Doc

_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


def _chunks(text: str, target: int = 700) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 1 > target:
            out.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        out.append(buf)
    return out


def load_documents(path: str | Path, target: int = 700) -> list[Doc]:
    """Read a text/markdown file or directory tree into chunked `Doc`s."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"docs path not found: {root}")
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    docs: list[Doc] = []
    for f in files:
        if f.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        stem = re.sub(r"[^A-Z0-9]+", "-", f.stem.upper()).strip("-") or "DOC"
        for i, chunk in enumerate(_chunks(text, target)):
            docs.append(Doc(id=f"{stem}-{i:03d}", title=f"{f.name} (part {i})", text=chunk))
    return docs
