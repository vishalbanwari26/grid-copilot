# Documentation for retrieval (`--docs`)

Drop real protocol specs or equipment manuals here as plain text, and the agent
will retrieve over them alongside the built-in notes:

```bash
python -m grid_copilot.cli --retriever vector --docs data/specs
python -m eval.hai_eval --train data/train1.csv.gz --provider groq \
  --retriever vector --docs data/specs
```

The loader (`grid_copilot/rag/loader.py`) reads `.txt` / `.md` files and chunks
them paragraph-aware into retrievable, citable passages.

## Adding the Modbus spec

The Modbus Application Protocol Specification is published free by the Modbus
Organization. Download the PDF and convert it to text next to this file:

```bash
pdftotext Modbus_Application_Protocol_V1_1b3.pdf data/specs/modbus.txt
```

Spec and manual files in this directory are gitignored (only this README is
tracked), so copyrighted documents stay local and are never committed.
