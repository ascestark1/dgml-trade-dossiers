#!/usr/bin/env python3
"""
pick_samples.py — choose annotated samples from the anchored trade records.

Picks dossiers that are worth showing someone: fully anchored on both chains,
recent, and covering different outcomes rather than four versions of the same
thing. Copies them into samples/ and writes their inputs into examples/ so
each one reproduces byte for byte.

    python3 pick_samples.py --trades trades_anchored.json
    python3 pick_samples.py --trades trades_anchored.json --n 6 --dry-run

Selection order:
    1. all three anchors present (Xenea commit + outcome, NVNM receipt)
    2. post-2026-07-05, so the outcome was measured by the fixed oracle
    3. one of each exit reason before repeating any
    4. most recent first within each group
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ANCHORS = ("xenea_commit_tx", "xenea_outcome_tx", "nvnm_receipt_tx")
ORACLE_FIX = "2026-07-05"


def score(t: dict) -> tuple:
    n = sum(1 for a in ANCHORS if t.get(a))
    return (n, t["committed_at"] >= ORACLE_FIX, t["committed_at"])


def main():
    a = sys.argv[1:]
    src = Path(a[a.index("--trades") + 1] if "--trades" in a else "trades_anchored.json")
    want = int(a[a.index("--n") + 1] if "--n" in a else 5)
    dry = "--dry-run" in a
    docset = Path("docset")

    if not src.exists():
        sys.exit(f"{src} not found — run backfill_anchors.py first")
    trades = json.loads(src.read_text())

    full = [t for t in trades if all(t.get(x) for x in ANCHORS)]
    post = [t for t in full if t["committed_at"] >= ORACLE_FIX]
    print(f"{len(trades)} records | {len(full)} fully anchored | "
          f"{len(post)} of those post-oracle-fix\n")

    pool = post or full
    if not pool:
        sys.exit("no fully anchored trades — nothing to pick")

    # one of each exit reason first, most recent within each
    by_exit: dict[str, list] = {}
    for t in sorted(pool, key=lambda x: x["committed_at"], reverse=True):
        by_exit.setdefault(t["exit_reason"], []).append(t)

    picked, i = [], 0
    while len(picked) < want and any(by_exit.values()):
        for reason in list(by_exit):
            if by_exit[reason] and len(picked) < want:
                picked.append(by_exit[reason].pop(0))
        i += 1
        if i > 20:
            break

    print(f"{'signal':<28}{'date':<12}{'dir':<6}{'tf':<5}{'session':<11}"
          f"{'exit':<13}{'pnl':>8}")
    for t in picked:
        print(f"{t['signal_id']:<28}{t['committed_at'][:10]:<12}{t['direction']:<6}"
              f"{t['timeframe']:<5}{t['session']:<11}{t['exit_reason']:<13}"
              f"{t['pnl_usd']:>+8.2f}")

    if dry:
        print("\n--dry-run: nothing copied")
        return

    Path("samples").mkdir(exist_ok=True)
    Path("examples").mkdir(exist_ok=True)
    copied = 0
    for t in picked:
        sid = t["signal_id"]
        srcf = docset / f"{sid}.dgml.xml"
        if not srcf.exists():
            print(f"  missing dossier for {sid} — skipped")
            continue
        shutil.copy(srcf, Path("samples") / srcf.name)
        short = sid.split("_")[-1]
        (Path("examples") / f"trade_{short}.json").write_text(
            json.dumps(t, indent=2) + "\n")
        copied += 1

    print(f"\ncopied {copied} dossiers to samples/ with inputs in examples/")
    print("verify each reproduces:")
    print("  for f in examples/*.json; do python3 dgml_emitter.py $f --out /tmp/rt; done")
    print("  diff -rq /tmp/rt samples/ | grep -v README")


if __name__ == "__main__":
    main()
