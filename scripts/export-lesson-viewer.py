#!/usr/bin/env python3
"""Freeze the local lesson-preview door into a deployable static directory."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import lesson_previews  # noqa: E402

ASSETS = ("lessons.html", "lessons.css", "lessons.js")
VERCEL = {
    "version": 2,
    "rewrites": [
        {"source": "/lessons", "destination": "/lessons.html"},
        {"source": "/api/lesson-previews", "destination": "/api/lesson-previews.json"},
    ],
    "headers": [{
        "source": "/(.*)",
        "headers": [
            {"key": "Cache-Control", "value": "no-store"},
            {"key": "Content-Security-Policy", "value": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'"},
            {"key": "Referrer-Policy", "value": "no-referrer"},
            {"key": "X-Content-Type-Options", "value": "nosniff"},
        ],
    }],
}


def export(receipts: pathlib.Path, catalog: pathlib.Path, output: pathlib.Path) -> dict:
    data = lesson_previews.build(receipts, catalog)
    if data.get("state") != "ok":
        raise ValueError(f"lesson preview export refused: {data.get('state')}: {data.get('detail', '')}")

    # A public static snapshot has no reason to disclose the builder's local path.
    data.pop("root", None)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as staging_name:
        staging = pathlib.Path(staging_name)
        for name in ASSETS:
            shutil.copyfile(ROOT / "client" / "phone" / name, staging / name)
        api = staging / "api"
        api.mkdir()
        (api / "lesson-previews.json").write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (staging / "vercel.json").write_text(
            json.dumps(VERCEL, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts-root", required=True, type=pathlib.Path)
    parser.add_argument("--catalog-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        data = export(args.receipts_root, args.catalog_root, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output.resolve()), "counts": data["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
