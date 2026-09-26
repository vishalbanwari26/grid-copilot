"""Score the deterministic DGA tools on real, labelled cases.

Data: github.com/alan-456/transformer-fault-dataset (gas concentrations from real
transformers with a diagnosed fault type, collected from Chinese utilities and
theses, including the IEC TC10 cases). It has no licence, so it is downloaded by
the user into data/dga/ and never committed:

    mkdir -p data/dga
    curl -L -o data/dga/data.xlsx https://raw.githubusercontent.com/alan-456/transformer-fault-dataset/main/data.xlsx

Each case has H2, CH4, C2H6, C2H4, C2H2 and a label. Labels map to the Duval /
IEC codes: PD, D1 (low-energy discharge), D2 (high-energy discharge), T1, T2, T3,
plus "normal".

Reported:
- exact accuracy of Duval triangle 1 and of the IEC ratio method on fault cases,
  and the share IEC leaves unclassified;
- coarse accuracy (PD vs discharge vs thermal), which is what decides urgency;
- what each method says about the normal cases. The triangle always returns a
  fault zone, even for healthy oil, which is why the agent only classifies a gas
  increase above a floor.

    python -m eval.dga_real_eval [--file data/dga/data.xlsx]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from grid_copilot.transformer.diagnostics import duval_triangle_1, iec_ratios
from grid_copilot.transformer.xlsx import read_xlsx

LABELS = {
    "局部放电": "PD", "低能放电": "D1", "高能放电": "D2",
    "低温过热": "T1", "中温过热": "T2", "高温过热": "T3", "正常": "normal",
}
COARSE = {"PD": "PD", "D1": "D", "D2": "D", "DT": "D", "T1": "T", "T2": "T", "T3": "T"}


def load_cases(path: Path) -> list[tuple[dict[str, float], str]]:
    rows = next(iter(read_xlsx(path).values()))
    header = rows[0]
    cases = []
    for r in rows[1:]:
        if len(r) < 6 or r[-1] not in LABELS:
            continue
        try:
            gas = {h: float(v) for h, v in zip(header[:5], r[:5])}
        except ValueError:
            continue
        cases.append((gas, LABELS[r[-1]]))
    return cases


def evaluate(cases: list[tuple[dict[str, float], str]]) -> dict:
    faults = [(g, y) for g, y in cases if y != "normal"]
    normals = [g for g, y in cases if y == "normal"]
    duval_ok = duval_coarse = iec_ok = iec_coarse = iec_unclassified = 0
    confusion: Counter[tuple[str, str]] = Counter()
    for g, y in faults:
        d = duval_triangle_1(g["CH4"], g["C2H4"], g["C2H2"])
        dz = d.zone if d else "none"
        confusion[(y, dz)] += 1
        duval_ok += dz == y
        duval_coarse += COARSE.get(dz) == COARSE[y]
        code = iec_ratios(g["H2"], g["CH4"], g["C2H6"], g["C2H4"], g["C2H2"]).code
        iec_ok += code == y
        iec_coarse += COARSE.get(code) == COARSE[y]
        iec_unclassified += code == "unclassified"
    n = len(faults)
    normal_zones = Counter((duval_triangle_1(g["CH4"], g["C2H4"], g["C2H2"]) or type("z", (), {"zone": "none"})).zone
                           for g in normals)
    per_class = {}
    for y in ("PD", "D1", "D2", "T1", "T2", "T3"):
        total = sum(c for (t, _), c in confusion.items() if t == y)
        if total:
            per_class[y] = {"n": total, "duval_correct": round(confusion[(y, y)] / total, 3),
                            "most_common_zone": max(((z, c) for (t, z), c in confusion.items() if t == y),
                                                    key=lambda x: x[1])[0]}
    return {
        "fault_cases": n, "normal_cases": len(normals),
        "duval_exact": round(duval_ok / n, 3), "duval_coarse": round(duval_coarse / n, 3),
        "iec_exact": round(iec_ok / n, 3), "iec_coarse": round(iec_coarse / n, 3),
        "iec_unclassified": round(iec_unclassified / n, 3),
        "per_class": per_class,
        "normal_cases_duval_zones": dict(normal_zones.most_common()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/dga/data.xlsx")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"{path} not found; see the module docstring for the download command")
    res = evaluate(load_cases(path))
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"{res['fault_cases']} labelled fault cases, {res['normal_cases']} normal cases ({path.name})")
    print(f"Duval triangle 1: exact {res['duval_exact']:.1%}, coarse (PD / discharge / thermal) {res['duval_coarse']:.1%}")
    print(f"IEC ratios:       exact {res['iec_exact']:.1%}, coarse {res['iec_coarse']:.1%}, "
          f"unclassified {res['iec_unclassified']:.1%}")
    print("Per class (Duval):")
    for y, v in res["per_class"].items():
        print(f"  {y}: n={v['n']:4d}  correct {v['duval_correct']:.0%}  most common zone {v['most_common_zone']}")
    print("Normal cases, Duval zone given:", res["normal_cases_duval_zones"])


if __name__ == "__main__":
    main()
