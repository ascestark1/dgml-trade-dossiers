#!/usr/bin/env python3
"""
query_docset.py — read the whole docset with XPath, no database.

The point of a docset is that every document shares one vocabulary, so a
question you can ask of one trade you can ask of all of them. This script is
the demonstration: plain XPath over plain files, no index, no parser written
for this repo, nothing but the standard.

    python3 query_docset.py docset/
    python3 query_docset.py docset/ --xpath '//docset:RealizedR'
    python3 query_docset.py docset/ --where session=Asian
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

NS = {
    "dg": "http://dgml.io/ns/dg#",
    "docset": "http://www.dgml.io/afritensor/trade-dossiers#",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
DGV = "{http://dgml.io/ns/dg#}value"


def value(doc, tag: str):
    """The canonical typed value of a concept element, or None.
    Tag matching is case-insensitive so `--where session=Asian` works as
    typed, without the caller needing to know the element is `Session`."""
    el = doc.find(f".//docset:{tag}", NS)
    if el is not None:
        return el.get(DGV)
    want = tag.lower()
    for e in doc.getroot().iter():
        if e.tag.startswith("{" + NS["docset"]) and \
                etree.QName(e).localname.lower() == want:
            return e.get(DGV)
    return None


def load(folder: str) -> list:
    return [etree.parse(str(p)) for p in sorted(Path(folder).glob("*.dgml.xml"))]


def summarise(docs: list) -> None:
    n = len(docs)
    if not n:
        print("no dossiers found")
        return

    rs, pnls = [], []
    wins = 0
    by_session, by_exit, by_tf = Counter(), Counter(), Counter()
    session_pnl = defaultdict(float)
    gate_verdicts = Counter()

    for d in docs:
        if value(d, "Win") == "true":
            wins += 1
        if (r := value(d, "RealizedR")) is not None:
            rs.append(float(r))
        if (p := value(d, "RealizedPnlUsd")) is not None:
            pnls.append(float(p))
        s = value(d, "Session") or "?"
        by_session[s] += 1
        session_pnl[s] += float(value(d, "RealizedPnlUsd") or 0)
        by_exit[value(d, "ExitReason") or "?"] += 1
        by_tf[value(d, "Timeframe") or "?"] += 1
        for g in d.findall(".//docset:GateVerdict", NS):
            name = (g.get("{http://www.w3.org/XML/1998/namespace}id") or "gate-?")
            gate_verdicts[f"{name[5:]}={g.get(DGV)}"] += 1

    print(f"AFRITENSOR TRADE DOSSIERS — {n} decision records\n")
    print(f"  win rate        {wins}/{n} = {wins/n*100:.1f}%")
    print(f"  net P&L         ${sum(pnls):+,.2f}")
    print(f"  mean realized   {statistics.mean(rs):+.3f}R")
    print(f"  median          {statistics.median(rs):+.3f}R")
    print(f"  best / worst    {max(rs):+.2f}R / {min(rs):+.2f}R\n")

    print("  by exit reason")
    for k, v in by_exit.most_common():
        print(f"    {k:<14} {v:>4}")

    print("\n  by session (count, net $)")
    for k, v in by_session.most_common():
        print(f"    {k:<14} {v:>4}   ${session_pnl[k]:+9.2f}")

    print("\n  by timeframe")
    for k, v in sorted(by_tf.items()):
        print(f"    {k:<14} {v:>4}")

    print("\n  gate verdicts recorded")
    for k, v in gate_verdicts.most_common(8):
        print(f"    {k:<34} {v:>4}")

    print("\n  Every figure above came from XPath over the files themselves.")
    print("  No database, no API, no trust in the party that produced them.")


def main() -> None:
    args = sys.argv[1:]
    folder = args[0] if args and not args[0].startswith("--") else "docset"
    docs = load(folder)

    if "--xpath" in args:
        expr = args[args.index("--xpath") + 1]
        for d in docs:
            for el in d.xpath(expr, namespaces=NS):
                sid = value(d, "SignalId")
                text = (el.text or "").strip() if hasattr(el, "text") else str(el)
                print(f"{sid}  {etree.QName(el).localname:<16} {text:>12}"
                      f"   [{el.get(DGV)}]")
        return

    if "--where" in args:
        key, _, want = args[args.index("--where") + 1].partition("=")
        docs = [d for d in docs if (value(d, key) or "").lower() == want.lower()]
        print(f"filter {key}={want}\n")

    summarise(docs)


if __name__ == "__main__":
    main()
