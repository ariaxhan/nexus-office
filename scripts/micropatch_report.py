"""#235 receipts -> the numbers acceptance 9 asks for: local success, escalation, disk."""

import argparse
import json
import statistics
import sys
from pathlib import Path


def summarize(rows):
    cases = len(rows)
    verified = sum(1 for r in rows if r.get("result") == "verified")
    escalated = sum(1 for r in rows if r.get("result") == "escalated")
    success_rate = verified / cases if cases else 0.0
    escalation_rate = escalated / cases if cases else 0.0
    walls = [r["wall_s"] for r in rows if "wall_s" in r]
    median_wall_s = statistics.median(walls) if walls else 0.0
    total_model_tokens = sum(r.get("model_tokens") or 0 for r in rows)
    disks = [r["disk_bytes"] for r in rows if "disk_bytes" in r]
    peak_disk_bytes = max(disks) if disks else 0
    residual_leaks = sum(len(r.get("leaked") or []) for r in rows)
    return {
        "cases": cases,
        "verified": verified,
        "escalated": escalated,
        "success_rate": success_rate,
        "escalation_rate": escalation_rate,
        "median_wall_s": median_wall_s,
        "total_model_tokens": total_model_tokens,
        "peak_disk_bytes": peak_disk_bytes,
        "residual_leaks": residual_leaks,
    }


def by_class(rows, classes):
    groups = {}
    for row in rows:
        commit = row.get("commit")
        if commit is None or commit not in classes:
            class_label = "unclassified"
        else:
            class_label = classes[commit]["class"]
        groups.setdefault(class_label, []).append(row)
    result = {}
    for label, group_rows in groups.items():
        result[label] = summarize(group_rows)
    return result


def load(paths):
    result = []
    for path in paths:
        p = Path(path)
        with p.open('r') as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(json.loads(line))  # a corrupt receipt is an error, not a skipped row
    return result


def main(argv):
    parser = argparse.ArgumentParser(description="#235 receipts report")
    parser.add_argument("paths", nargs="+", help="one or more JSONL receipt files")
    parser.add_argument("--classes", metavar="FILE", help="JSON object mapping commit -> {\"class\": label}")
    args = parser.parse_args(argv)

    rows = load(args.paths)
    out = {"all": summarize(rows)}
    if args.classes is not None:
        with open(args.classes, "r") as f:
            classes = json.load(f)
        out["by_class"] = by_class(rows, classes)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
