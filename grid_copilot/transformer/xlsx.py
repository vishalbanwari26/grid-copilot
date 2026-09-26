"""A minimal .xlsx reader (standard library only) for the public DGA datasets.

Reads every worksheet into rows of strings, resolving shared strings. Enough for
plain tabular sheets; formulas, dates and styles are not interpreted.
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx(path: str | Path) -> dict[str, list[list[str]]]:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        out: dict[str, list[list[str]]] = {}
        for name in sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet")):
            root = ET.fromstring(z.read(name))
            rows = []
            for r in root.iter(f"{_NS}row"):
                row = []
                for c in r.findall(f"{_NS}c"):
                    v = c.find(f"{_NS}v")
                    val = v.text if v is not None and v.text is not None else ""
                    if c.get("t") == "s" and val:
                        val = shared[int(val)]
                    elif c.get("t") == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
                    row.append(val)
                rows.append(row)
            out[name.rsplit("/", 1)[-1].removesuffix(".xml")] = rows
        return out
