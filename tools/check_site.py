"""公開前の最終チェック: 公開ディレクトリ(site/)が壊れていないかを検証する。

  python -m tools.check_site site
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

REQUIRED = ["index.html", "app.js", "app.css", "lib.js", "sw.js", "manifest.webmanifest", "station.json", "episodes/index.json"]
MAX_SITE_MB = 900  # GitHub Pages の上限 1GB に対して余裕を持たせる


def check(site: Path) -> list[str]:
    errs: list[str] = []
    for r in REQUIRED:
        if not (site / r).is_file():
            errs.append(f"missing {r}")
    if errs:
        return errs
    index = json.loads((site / "episodes/index.json").read_text(encoding="utf-8"))
    for s in index["episodes"]:
        ep_path = site / "episodes" / f"{s['id']}.json"
        if not ep_path.is_file():
            errs.append(f"index has {s['id']} but json missing")
            continue
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
        lines = ep["lines"]
        if not lines:
            errs.append(f"{ep['id']}: no lines")
        prev_end = 0.0
        for i, ln in enumerate(lines):
            if ln["speaker"] not in ep["speakers"]:
                errs.append(f"{ep['id']}: line {i} unknown speaker")
            if ep.get("audio"):
                if not (ln["end"] > ln["start"] >= prev_end - 1e-6):
                    errs.append(f"{ep['id']}: line {i} timing not monotonic ({ln['start']}-{ln['end']})")
                    break
                prev_end = ln["end"]
        if ep.get("audio"):
            a = site / ep["audio"]
            if not a.is_file() or a.stat().st_size < 1000:
                errs.append(f"{ep['id']}: audio missing or empty")
            elif a.suffix == ".wav":
                with wave.open(str(a)) as w:
                    dur = w.getnframes() / w.getframerate()
                if abs(dur - ep["duration"]) > 0.5:
                    errs.append(f"{ep['id']}: duration mismatch wav={dur:.2f} json={ep['duration']}")
            if lines and lines[-1]["end"] > ep["duration"] + 0.01:
                errs.append(f"{ep['id']}: last line ends after audio")
        ids = {s2["id"] for s2 in ep["sources"]}
        for c in ep["chapters"]:
            if any(x not in ids for x in c["source_ids"]):
                errs.append(f"{ep['id']}: chapter refers unknown source")
    total = sum(p.stat().st_size for p in site.rglob("*") if p.is_file()) / 1e6
    if total > MAX_SITE_MB:
        errs.append(f"site too large: {total:.0f}MB")
    return errs


def main() -> int:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    errs = check(site)
    if errs:
        print("NG\n" + "\n".join(f" - {e}" for e in errs))
        return 1
    print(f"OK: {site} looks good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
