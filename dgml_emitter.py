#!/usr/bin/env python3
"""
dgml_emitter.py — render a resolved Zcastor trade as a DGML dossier.

Conforms to DGML spec v0.7 (github.com/dgml-io/dgml-spec), docset
"Afritensor Trade Dossier" (schema.rnc alongside this file).

DESIGN CONSTRAINTS (deliberate):
  - OFFLINE ONLY. Nothing here runs in the signal or execution path. It
    reads records that already exist and writes files. It never touches
    MT5, the bridge, or the resolver.
  - NO FABRICATION. A field that isn't known is omitted, never guessed.
    Absent provenance transactions produce no element at all.
  - BORN DIGITAL. These records have no source page, so no element carries
    a spatial dg:origin (spec §7: elements with no source match omit it).
    Derived fields carry dg:origin="computed" plus dg:itemprop/dg:href to
    the elements they were computed from (spec §7 reserved value).

USAGE
    from dgml_emitter import emit_dossier
    path = emit_dossier(trade_record, out_dir="data/dgml")

    # or from the command line, over a JSON file of trade records:
    python3 dgml_emitter.py trades.json --out data/dgml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lxml import etree

# ── namespaces (spec §4) ─────────────────────────────────────────────────
DG = "http://dgml.io/ns/dg#"
DOCSET = "http://www.dgml.io/afritensor/trade-dossiers#"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML = "http://www.w3.org/XML/1998/namespace"

NSMAP = {"dg": DG, "docset": DOCSET, "xsi": XSI}

# Exit reasons we recognise; anything else passes through as-is.
EXIT_REASONS = {"take_profit", "stop_loss", "max_hold", "day_boundary", "manual"}


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _el(parent, ns: str, tag: str, text: str | None = None, **attrs):
    """Create a child element. Attribute keys use dg_/xsi_/xml_ prefixes."""
    node = etree.SubElement(parent, _q(ns, tag))
    for key, val in attrs.items():
        if val is None:
            continue
        if key.startswith("dg_"):
            node.set(_q(DG, key[3:]), str(val))
        elif key.startswith("xsi_"):
            node.set(_q(XSI, key[4:]), str(val))
        elif key == "xml_id":
            node.set(_q(XML, "id"), str(val))
        else:
            node.set(key, str(val))
    if text is not None:
        node.text = str(text)
    return node


def _typed(parent, tag: str, display: str, value: Any, xsd_type: str, **attrs):
    """A concept element carrying a canonical typed value (spec §6)."""
    return _el(parent, DOCSET, tag, display,
               xsi_type=xsd_type, dg_value=value, **attrs)


def _price(parent, tag: str, price: float, **attrs):
    if price is None:
        return None
    return _typed(parent, tag, f"{price:,.2f}", f"{price:.2f}", "decimal", **attrs)


def build_dossier(t: dict) -> etree._Element:
    """Build the DGML element tree for one resolved trade."""
    root = etree.Element(_q(DG, "chunk"), nsmap=NSMAP)

    dossier = _el(root, DOCSET, "TradeDossier",
                  dg_structure="section", xml_id="dossier")

    # A re-entry dossier points at the trade it follows (spec §8 link pattern).
    if t.get("parent_dossier_ref"):
        dossier.set(_q(DG, "itemprop"), "follows")
        dossier.set(_q(DG, "href"), t["parent_dossier_ref"])

    _el(dossier, DG, "chunk", f"Trade Dossier — {t['signal_id']}",
        dg_structure="header")

    # ── THESIS — committed before the outcome was known ──────────────────
    thesis = _el(dossier, DOCSET, "Thesis",
                 dg_structure="section", xml_id="thesis")
    _el(thesis, DG, "chunk", "Committed before the outcome was known",
        dg_structure="header")

    direction = str(t["direction"]).lower()
    _el(thesis, DOCSET, "Direction", direction,
        dg_value=direction, xml_id="direction")
    _el(thesis, DOCSET, "Timeframe", t["timeframe"], dg_value=t["timeframe"])
    _el(thesis, DOCSET, "Session", t["session"], dg_value=t["session"])
    if t.get("confidence"):
        _el(thesis, DOCSET, "ConfidenceTier", t["confidence"],
            dg_value=t["confidence"])
    if t.get("regime"):
        _el(thesis, DOCSET, "Regime", t["regime"], dg_value=t["regime"])
    _typed(thesis, "CommittedAt", t["committed_at"], t["committed_at"],
           "dateTime", xml_id="committed-at")

    # ── GATES — the full admission trail ─────────────────────────────────
    gates = t.get("gates") or []
    if gates:
        gates_el = _el(dossier, DOCSET, "Gates",
                       dg_structure="section", xml_id="gates")
        _el(gates_el, DG, "chunk", "Gate verdicts", dg_structure="header")
        seen: dict[str, int] = {}
        for g in gates:
            # The gate's identity lives in xml:id and the display text; the
            # verdict is the canonical value. No custom attributes — the spec
            # carries meaning in elements plus dg:/xsi: attributes only.
            # xml:id must be unique per document, so a gate that legitimately
            # fires twice gets -2, -3 ... rather than colliding.
            slug = str(g["name"]).strip().lower().replace("_", "-")
            seen[slug] = seen.get(slug, 0) + 1
            xid = f"gate-{slug}" if seen[slug] == 1 else f"gate-{slug}-{seen[slug]}"
            _el(gates_el, DOCSET, "GateVerdict",
                f"{g['name']} — {g['detail']}",
                dg_structure="li", dg_value=g["verdict"], xml_id=xid)

    # ── EXECUTION — what the broker actually received ────────────────────
    ex = _el(dossier, DOCSET, "Execution",
             dg_structure="section", xml_id="execution")
    _el(ex, DG, "chunk", "Execution", dg_structure="header")

    if t.get("entry_mode"):
        _el(ex, DOCSET, "EntryMode", t["entry_mode"], dg_value=t["entry_mode"])
    if t.get("trigger_price") is not None:
        _price(ex, "TriggerPrice", t["trigger_price"], xml_id="trigger-price")
    _price(ex, "FillPrice", t["fill_price"], xml_id="fill-price")
    _price(ex, "StopLoss", t["stop_loss"], xml_id="stop-loss")
    if t.get("take_profit") is not None:
        _price(ex, "TakeProfit", t["take_profit"], xml_id="take-profit")

    # RiskPoints is DERIVED from fill and stop -> computed origin + links
    risk_pts = abs(float(t["fill_price"]) - float(t["stop_loss"]))
    _typed(ex, "RiskPoints", f"{risk_pts:,.0f} pts", f"{risk_pts:.2f}", "decimal",
           dg_origin="computed", dg_itemprop="computedFrom",
           dg_href="#fill-price; #stop-loss", xml_id="risk-points")

    _typed(ex, "Volume", f"{float(t['volume']):.2f} lots",
           f"{float(t['volume']):.2f}", "decimal")
    if t.get("ticket"):
        _typed(ex, "BrokerTicket", str(t["ticket"]), str(t["ticket"]), "string")

    # ── OUTCOME — measured against the committed thesis ──────────────────
    out = _el(dossier, DOCSET, "Outcome", dg_structure="section",
              xml_id="outcome", dg_itemprop="resolves", dg_href="#thesis")
    _el(out, DG, "chunk", "Measured outcome", dg_structure="header")

    win = bool(t["win"])
    _typed(out, "Win", "yes" if win else "no", "true" if win else "false",
           "boolean", xml_id="win")
    _price(out, "ExitPrice", t["exit_price"], xml_id="exit-price")
    if t.get("exit_reason"):
        _el(out, DOCSET, "ExitReason", t["exit_reason"].replace("_", " "),
            dg_value=t["exit_reason"])
    pnl = float(t["pnl_usd"])
    _typed(out, "RealizedPnlUsd", f"{'-' if pnl < 0 else ''}${abs(pnl):,.2f}",
           f"{pnl:.2f}", "decimal", xml_id="realized-pnl")

    # RealizedR is DERIVED -> computed origin + links to its inputs
    move = (float(t["fill_price"]) - float(t["exit_price"])) if direction in (
        "down", "sell", "short") else (
        float(t["exit_price"]) - float(t["fill_price"]))
    realized_r = move / risk_pts if risk_pts else 0.0
    _typed(out, "RealizedR", f"{realized_r:+.2f}R", f"{realized_r:.4f}", "decimal",
           dg_origin="computed", dg_itemprop="computedFrom",
           dg_href="#fill-price; #exit-price; #risk-points")

    _typed(out, "ResolvedAt", t["resolved_at"], t["resolved_at"], "dateTime")

    if t.get("measurement_caveat"):
        _el(out, DOCSET, "MeasurementCaveat", t["measurement_caveat"],
            dg_value=t["measurement_caveat"])

    # ── PROVENANCE — the chain records attesting this trade ──────────────
    prov = _el(dossier, DOCSET, "Provenance",
               dg_structure="section", xml_id="provenance")
    _el(prov, DG, "chunk", "On-chain provenance", dg_structure="header")

    _typed(prov, "SignalId", t["signal_id"], t["signal_id"], "string")
    if t.get("entry_signal_id"):
        _typed(prov, "EntrySignalId", t["entry_signal_id"],
               t["entry_signal_id"], "string")

    # Absent transactions are OMITTED, never invented.
    # The chain is identified by the element name itself (XeneaCommitTx,
    # NvnmReceiptTx), so no extra attribute is needed.
    for tag, key in (
        ("XeneaCommitTx", "xenea_commit_tx"),
        ("XeneaOutcomeTx", "xenea_outcome_tx"),
        ("NvnmReceiptTx", "nvnm_receipt_tx"),
    ):
        tx = t.get(key)
        if tx:
            _typed(prov, tag, tx, tx, "string")

    return root


def emit_dossier(trade: dict, out_dir: str = "data/dgml") -> Path:
    """Render one trade to <out_dir>/<signal_id>.dgml.xml. Returns the path."""
    root = build_dossier(trade)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"{trade['signal_id']}.dgml.xml"
    tree = etree.ElementTree(root)
    etree.indent(tree, space="  ")
    tree.write(str(path), xml_declaration=True, encoding="utf-8",
               pretty_print=True)
    return path


def canonical_hash(path: str | Path) -> str:
    """SHA-256 over exclusive C14N of the document — the value a receipt's
    contentHash should bind to (spec §10 element/file hashing convention)."""
    import hashlib
    from io import BytesIO
    doc = etree.parse(str(path))
    buf = BytesIO()
    doc.write_c14n(buf, exclusive=True, with_comments=False)
    return hashlib.sha256(buf.getvalue()).hexdigest()


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = "data/dgml"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    records = json.loads(src.read_text())
    if isinstance(records, dict):
        records = [records]
    for rec in records:
        p = emit_dossier(rec, out)
        print(f"{p}  sha256={canonical_hash(p)}")


if __name__ == "__main__":
    main()
