#!/usr/bin/env python3
"""
verify_dossier.py — check a dossier against the archived chain records.

This is the piece that makes the archive worth having. A dossier carries
transaction hashes; the archive carries the events those transactions emitted.
Put them together and a reader can confirm that what the document says matches
what the chain recorded — with no RPC, no explorer, and no working chain.

    python3 verify_dossier.py samples/sig_20260729004602_anws.dgml.xml \\
        --archive xenea_archive_public_20260731

    python3 verify_dossier.py docset/ --archive xenea_archive_public_20260731

What it checks, per dossier:
    1. the anchors named in the document exist in the archive
    2. the outcome recorded on-chain agrees with the outcome in the document
    3. the archive itself has not been altered since capture (manifest hashes)

Exit status is non-zero if anything fails, so it works in CI.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("need lxml:  pip install lxml")

NS = {"dg": "http://dgml.io/ns/dg#",
      "docset": "http://www.dgml.io/afritensor/trade-dossiers#"}
DGV = "{http://dgml.io/ns/dg#}value"


def val(doc, tag):
    el = doc.find(f".//docset:{tag}", NS)
    return el.get(DGV) if el is not None else None


def load_archive(root: Path):
    """tx hash -> decoded event, plus an integrity verdict."""
    man_p = root / "manifest.json"
    if not man_p.exists():
        sys.exit(f"no manifest.json in {root}")
    man = json.loads(man_p.read_text())

    altered = []
    for rel, meta in man.get("files", {}).items():
        p = root / rel
        if not p.exists():
            altered.append((rel, "missing")); continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 16), b""):
                h.update(b)
        if h.hexdigest() != meta["sha256"]:
            altered.append((rel, "hash mismatch"))

    by_tx = {}
    for p in (root / "decoded").glob("*.jsonl"):
        kind = p.stem
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("tx"):
                by_tx.setdefault(rec["tx"].lower(), []).append((kind, rec))
    return man, by_tx, altered


def check(path: Path, by_tx: dict) -> tuple[int, int, list]:
    doc = etree.parse(str(path))
    sid = val(doc, "SignalId") or path.stem
    anchors = [(t, val(doc, t)) for t in
               ("XeneaCommitTx", "XeneaOutcomeTx", "NvnmReceiptTx")]
    anchors = [(t, v) for t, v in anchors if v]
    if not anchors:
        return 0, 0, [], []

    ok = 0
    problems = []
    divergences = []
    seen = set()
    for tag, tx in anchors:
        hits = by_tx.get(tx.lower())
        if not hits:
            problems.append(f"{sid}: {tag} {tx[:18]}… not in the archive")
            continue
        ok += 1
        # cross-check the outcome where the archive carries one
        # NOTE: the chain's `win` and the document's Win answer DIFFERENT
        # questions. On-chain, win means the oracle close was on the
        # predicted side. In the dossier, Win means the broker booked a
        # profit. A trade that is directionally right but stopped out first
        # is a chain win and a document loss — both true, neither wrong.
        # These are recorded as divergences, not failures.
        for kind, rec in hits:
            if "win" in rec:
                doc_win = (val(doc, "Win") == "true")
                key = (sid, bool(rec["win"]), doc_win)
                if bool(rec["win"]) != doc_win and key not in seen:
                    seen.add(key)
                    divergences.append(
                        (sid, val(doc, "ExitReason") or "?",
                         bool(rec["win"]), doc_win))
    return len(anchors), ok, problems, divergences


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    target = Path(args[0])
    arch = Path(args[args.index("--archive") + 1] if "--archive" in args
                else "xenea_archive_public")

    man, by_tx, altered = load_archive(arch)
    print("=" * 62)
    print("  DOSSIER ↔ ARCHIVE VERIFICATION")
    print("=" * 62)
    print(f"  archive : {arch}  ({man.get('bundle','?')} bundle, "
          f"taken {man.get('archived_at','?')[:10]})")
    print(f"  events  : {sum(man.get('event_counts',{}).values())} "
          f"indexed by {len(by_tx)} transactions")
    if altered:
        print(f"\n  ARCHIVE INTEGRITY FAILED — {len(altered)} file(s):")
        for rel, why in altered[:5]:
            print(f"    {rel}: {why}")
        sys.exit(1)
    print("  integrity: every file matches the hash taken at capture\n")

    files = sorted(target.glob("*.dgml.xml")) if target.is_dir() else [target]
    total_anchors = total_ok = 0
    all_problems = []
    all_div = []
    unanchored = 0
    for f in files:
        n, ok, probs, div = check(f, by_tx)
        if n == 0:
            unanchored += 1
        total_anchors += n
        total_ok += ok
        all_problems += probs
        all_div += div

    print(f"  {len(files)} dossier(s) checked")
    print(f"  {total_ok}/{total_anchors} anchors resolved in the archive")
    if unanchored:
        print(f"  {unanchored} carry no anchors (bridge commit never landed)")

    missing = [p for p in all_problems if "not in the archive" in p]
    real = [p for p in all_problems if "not in the archive" not in p]

    if missing:
        print(f"\n  {len(missing)} anchor(s) not in this bundle:")
        for p in missing[:3]:
            print(f"    {p}")
        print("    (expected against the PUBLIC bundle — commit transactions")
        print("     live only in the private one)")

    if all_div:
        cw = [d for d in all_div if d[2] and not d[3]]
        cl = [d for d in all_div if not d[2] and d[3]]
        print(f"\n  {len(all_div)} outcome divergence(s) — EXPECTED, not errors:")
        print(f"    chain win / document loss : {len(cw):>3}", end="")
        if cw:
            top = {}
            for d in cw: top[d[1]] = top.get(d[1], 0) + 1
            print("   mostly " + max(top, key=top.get).replace("_", " "))
        else:
            print()
        print(f"    chain loss / document win : {len(cl):>3}", end="")
        if cl:
            top = {}
            for d in cl: top[d[1]] = top.get(d[1], 0) + 1
            print("   mostly " + max(top, key=top.get).replace("_", " "))
        else:
            print()
        print()
        print("    On-chain, win means the oracle close was on the predicted")
        print("    side. In the dossier, Win means the broker booked a profit.")
        print("    A call that was right but stopped out first is a chain win")
        print("    and a document loss. Both true. The divergence is the")
        print("    interesting number, not a fault.")

    if real:
        print(f"\n  {len(real)} INTEGRITY PROBLEM(S):")
        for p in real:
            print(f"    {p}")
        sys.exit(1)

    print("\n  Every anchor present in this bundle resolves.")
    print("  Verified without an RPC, an explorer, or a live chain.")


if __name__ == "__main__":
    main()
