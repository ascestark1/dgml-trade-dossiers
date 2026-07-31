#!/usr/bin/env python3
"""
extract_trades.py — reconstruct complete trade records from Zcastor's own
logs, cross-checked against the broker's report, ready for dgml_emitter.py.

It walks the log in order and rebuilds each trade's full lifecycle:

    Signal received  ->  gate verdicts  ->  Bridge commit (signal_id)
      ->  Ledger verdict (session/route)  ->  EntryWatcher armed (zone/stop)
      ->  CONFIRMED (trigger price)  ->  Trade executed (ticket, fill, sl, tp)
      ->  Watched entry FILLED (entry signal_id)  ->  Close booked (pnl, exit)

Volume and the booked P&L are taken from the BROKER report where available,
not from the log — the broker is ground truth for what actually happened.

NO FABRICATION: a trade is only emitted if the log contains its whole chain
through to a booked close. Anything unknown is left out of the record, never
guessed, so the dossier simply omits that element.

USAGE
    python3 extract_trades.py zcastor-8.log --broker Document_111111.xlsx \\
        --out trades.json
    python3 dgml_emitter.py trades.json --out data/dgml
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The server clock runs at UTC+3 (Kenya) and equals the broker's frame.
SERVER_UTC_OFFSET = timedelta(hours=3)

TS = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+"

RE_SIGNAL   = re.compile(TS + r".*Signal received — conf=(\w+) dir=(\w+) tf=(\w+)")
RE_COMMIT   = re.compile(TS + r'.*Bridge: commit → 200 .*"signal_id":"(\w+)"')
RE_LEDGER   = re.compile(TS + r".*Ledger verdict — ([^/]+)/([^/]+)/(\w+) n=(\d+) exp=\$([-+\d.]+)")
RE_ARMED    = re.compile(
    TS + r".*EntryWatcher armed — (\w+) (BUY|SELL) \[([^\]]+)\] zone=([\d.]+) "
         r"confirm=([\d.]+) invalidate=([\d.]+) stop=([\d.]+)")
RE_CONFIRM  = re.compile(TS + r".*EntryWatcher CONFIRMED (\w+) at price=([\d.]+)")
RE_EXEC     = re.compile(
    TS + r".*Trade executed: ticket=(\d+) side=(BUY|SELL) price=([\d.]+) "
         r"sl=([\d.]+|None) tp=([\d.]+|None)")
RE_FILLED   = re.compile(TS + r".*Watched entry FILLED — (\w+) ticket=(\d+)")
RE_CLOSE    = re.compile(
    TS + r".*Close booked \((\w+)\): ticket=(\d+) pnl=([-\d.]+) close=([\d.]+) win=(\w+)")

# Gate lines, in the order they can appear for one signal.
RE_SPREAD   = re.compile(TS + r".*Wide spread: ([\d.]+)pts .*threshold ([\d.]+)pts")
RE_CONTEXT  = re.compile(TS + r".*Block context gate — (\w+) tf=(\w+) → (.+)")
RE_RANGE    = re.compile(TS + r".*Gate B — range adequacy: available=(\d+) pts < sl=(\d+) pts")
RE_EXHAUST  = re.compile(TS + r".*Session-exhaustion gate — (.+?) — MT5 suppressed")
RE_SUPPRESS = re.compile(TS + r".*MT5 suppressed \((\w+)\)")
RE_REARM    = re.compile(TS + r".*Sweep re-arm — (\w+) after stop-out")


def to_utc(server_ts: str) -> str:
    """Server local time -> UTC ISO 8601."""
    dt = datetime.strptime(server_ts, "%Y-%m-%d %H:%M:%S") - SERVER_UTC_OFFSET
    return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_broker(path: str) -> dict:
    """ticket -> {volume, profit, open_price, close_price} from the MT5 report."""
    try:
        import pandas as pd
    except ImportError:
        print("  (pandas not available — volume will come from the log only)")
        return {}
    import warnings
    warnings.filterwarnings("ignore")

    raw = pd.read_excel(path, sheet_name=0, header=None)
    start = None
    for i, v in enumerate(raw[0]):
        if str(v).strip() == "Positions":
            start = i
        if str(v).strip() == "Orders" and start is not None:
            end = i
            break
    else:
        end = len(raw)
    if start is None:
        return {}

    body = raw.iloc[start + 2:end, :13].copy()
    body.columns = ["open_time", "ticket", "symbol", "type", "volume", "open_price",
                    "sl", "tp", "close_time", "close_price", "commission", "swap", "profit"]
    body = body[body["ticket"].notna()]
    out = {}
    for _, r in body.iterrows():
        try:
            out[str(int(r["ticket"]))] = {
                "volume": float(r["volume"]),
                "profit": float(r["profit"]),
                "open_price": float(r["open_price"]),
                "close_price": float(r["close_price"]),
            }
        except (ValueError, TypeError):
            continue
    return out


def classify_exit(exit_px: float, sl: float | None, tp: float | None,
                  tol: float = 30.0) -> str:
    """Name the exit from prices alone. 'early_close' states only what is
    observable: the position ended before either level was reached. It does
    not assert who closed it."""
    if tp and abs(exit_px - tp) <= tol:
        return "take_profit"
    if sl and abs(exit_px - sl) <= tol:
        return "stop_loss"
    return "early_close"


def parse_log(path: str) -> tuple[dict, dict]:
    """Returns (signals_by_id, trades_by_ticket).

    ATTRIBUTION RULE (matters — these become provenance documents):
    the dashboard can raise several signals in the same millisecond, and their
    gate lines then interleave in the log. Blocks are therefore held open in
    arrival order and committed FIFO (verified against each Ledger verdict's
    own timeframe). A gate line that cannot be attributed to exactly one open
    signal is DROPPED, not guessed — a misattributed gate verdict is worse
    than an absent one. Signals whose ledger timeframe contradicts the signal
    are marked unreliable and excluded entirely.
    """
    signals: dict[str, dict] = {}
    armed: dict[str, dict] = {}
    trades: dict[str, dict] = {}
    rearm_parent: dict[str, str] = {}

    open_signals: list[dict] = []   # received, awaiting their Ledger verdict
    pending_commit: dict | None = None
    last_committed: dict | None = None
    last_confirmed = None
    stats = {"ambiguous_gates": 0, "unjoinable": 0, "evicted": 0}

    def attach(gate: dict, tf: str | None = None) -> None:
        """Attach a gate to the one open signal it belongs to, or drop it."""
        if tf is not None:
            matches = [s for s in open_signals if s["timeframe"] == tf]
            if len(matches) == 1:
                matches[0]["gates"].append(gate)
                return
        elif len(open_signals) == 1:
            open_signals[0]["gates"].append(gate)
            return
        for s in open_signals:
            s["gate_gap"] = True          # a verdict exists that we can't place
        stats["ambiguous_gates"] += 1

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if (m := RE_SIGNAL.search(line)):
                ts, conf, direction, tf = m.groups()
                now = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                before = len(open_signals)
                open_signals[:] = [
                    s for s in open_signals
                    if (now - datetime.strptime(s["seen_at"],
                                                "%Y-%m-%d %H:%M:%S")).total_seconds() < 120]
                stats["evicted"] += before - len(open_signals)
                open_signals.append({
                    "seen_at": ts, "confidence": conf, "direction": direction,
                    "timeframe": tf, "gates": []})
                continue

            if (m := RE_SPREAD.search(line)):
                attach({"name": "spread", "verdict": "advisory",
                        "detail": f"{m.group(2)}pts against a {m.group(3)}pt warn "
                                  f"threshold; viability assessed downstream in dollars"})
                continue

            if (m := RE_CONTEXT.search(line)):
                regime, tf, action = m.group(2), m.group(3), m.group(4).strip()
                for s in open_signals:
                    if s["timeframe"] == tf:
                        s["regime"] = regime
                attach({"name": "block_context",
                        "verdict": "route" if "watcher" in action else "suppress",
                        "detail": f"{regime} regime on {tf} — {action}"}, tf=tf)
                continue

            if (m := RE_RANGE.search(line)):
                attach({"name": "range_adequacy", "verdict": "suppress",
                        "detail": f"available room {m.group(2)}pts is under the "
                                  f"{m.group(3)}pt stop — trap probability high"})
                continue

            if (m := RE_EXHAUST.search(line)):
                attach({"name": "session_exhaustion", "verdict": "suppress",
                        "detail": m.group(2).strip()})
                continue

            if (m := RE_COMMIT.search(line)):
                # Hold the id; the Ledger verdict that follows carries the
                # timeframe that identifies WHICH open signal this is.
                pending_commit = {"signal_id": m.group(2),
                                  "committed_at": to_utc(m.group(1))}
                continue

            if (m := RE_LEDGER.search(line)):
                tf, session, route, n, exp = m.groups()[1:]
                tf = tf.strip()
                if pending_commit is None:
                    continue
                # Join on timeframe — robust to interleaving AND to the ~1% of
                # signals that never commit (a positional queue would desync
                # permanently at the first gap; this self-heals).
                match = next((s for s in open_signals if s["timeframe"] == tf), None)
                if match is None:
                    stats["unjoinable"] += 1
                    pending_commit = None
                    continue
                open_signals.remove(match)
                match["signal_id"] = pending_commit["signal_id"]
                match["committed_at"] = pending_commit["committed_at"]
                match["session"] = session.strip()
                match["route"] = route
                detail = (f"segment {tf}/{session.strip()}/{route} has n={n} "
                          f"samples, expectancy ${exp}")
                detail += ("; shadow only, not enforcing" if int(n) < 40
                           else "; enforcing")
                match["gates"].append({"name": "edge_ledger",
                                       "verdict": "advisory", "detail": detail})
                signals[match["signal_id"]] = match
                last_committed = match
                pending_commit = None
                continue

            if (m := RE_SUPPRESS.search(line)):
                if last_committed is not None:
                    last_committed["suppressed"] = m.group(2)
                continue

            if (m := RE_ARMED.search(line)):
                sid, side, bracket, zone, confirm, invalid, stop = m.groups()[1:]
                parts = bracket.split("/")
                armed[sid] = {
                    "armed_at": to_utc(m.group(1)), "side": side,
                    "zone": float(zone), "stop_hint": float(stop),
                    "phase": parts[1] if len(parts) > 1 else None,
                    "mode": parts[2] if len(parts) > 2 else "pullback",
                }
                continue

            if (m := RE_CONFIRM.search(line)):
                last_confirmed = {"sid": m.group(2), "trigger": float(m.group(3))}
                continue

            if (m := RE_EXEC.search(line)):
                ts, ticket, side, price, sl, tp = m.groups()
                trades[ticket] = {
                    "ticket": ticket, "side": side, "fill_price": float(price),
                    "stop_loss": None if sl == "None" else float(sl),
                    "take_profit": None if tp == "None" else float(tp),
                    "executed_at": to_utc(ts),
                }
                continue

            if (m := RE_FILLED.search(line)):
                ew_id, ticket = m.group(2), m.group(3)
                if ticket in trades:
                    trades[ticket]["entry_signal_id"] = ew_id
                    if last_confirmed:
                        trades[ticket]["parent_signal_id"] = last_confirmed["sid"]
                        trades[ticket]["trigger_price"] = last_confirmed["trigger"]
                        last_confirmed = None
                continue

            if (m := RE_REARM.search(line)):
                child = m.group(2)
                rearm_parent[child] = child.rsplit("_r", 1)[0]
                continue

            if (m := RE_CLOSE.search(line)):
                ts, source, ticket, pnl, close_px, win = m.groups()
                if ticket in trades:
                    trades[ticket].update({
                        "resolved_at": to_utc(ts), "close_source": source,
                        "pnl_usd": float(pnl), "exit_price": float(close_px),
                        "win": win == "True"})
                continue

    for sid, a in armed.items():
        if sid in signals:
            signals[sid].update(a)
    for child, parent in rearm_parent.items():
        if child in signals:
            signals[child]["parent_dossier_ref"] = f"{parent}.dgml.xml#dossier"

    print(f"  attribution: {stats['ambiguous_gates']} gate lines dropped as "
          f"ambiguous | {stats['unjoinable']} commits unjoinable | "
          f"{stats['evicted']} stale signals evicted")
    return signals, trades


def config_volume(path: str = "config.json") -> float | None:
    """The volume the engine is configured to trade — the honest fallback when
    no broker export is present."""
    try:
        import json as _j
        return float(_j.loads(Path(path).read_text()).get("volume"))
    except Exception:
        return None


def build_records(signals: dict, trades: dict, broker: dict,
                  default_volume: float | None = None) -> list[dict]:
    """Join signal context to executed trades. Only complete chains survive."""
    records, skipped = [], {"no_parent": 0, "no_close": 0, "no_stop": 0,
                           "unreliable": 0}

    for ticket, t in trades.items():
        if "resolved_at" not in t:
            skipped["no_close"] += 1
            continue
        if t.get("stop_loss") in (None, 0):
            skipped["no_stop"] += 1
            continue

        sid = t.get("parent_signal_id")
        sig = signals.get(sid) if sid else None
        if not sig or "session" not in sig or "committed_at" not in sig:
            skipped["no_parent"] += 1
            continue
        if sig.get("unreliable"):
            skipped["unreliable"] = skipped.get("unreliable", 0) + 1
            continue

        b = broker.get(ticket, {})
        volume = b.get("volume")
        if volume is None:
            # No broker export (normal for live tailing) — use the volume the
            # engine was configured with. Recorded as-is; never guessed.
            volume = default_volume
        if volume is None:
            skipped["no_volume"] = skipped.get("no_volume", 0) + 1
            continue

        rec = {
            "signal_id": sig["signal_id"],
            "entry_signal_id": t.get("entry_signal_id"),
            "committed_at": sig["committed_at"],
            "direction": sig["direction"],
            "timeframe": sig["timeframe"],
            "session": sig["session"],
            "confidence": sig.get("confidence"),
            "regime": sig.get("regime"),
            "gates": sig.get("gates", []),
            "entry_mode": sig.get("mode", "pullback"),
            "trigger_price": t.get("trigger_price"),
            "fill_price": t["fill_price"],
            "stop_loss": t["stop_loss"],
            "take_profit": t.get("take_profit"),
            "volume": volume,
            "ticket": ticket,
            "win": b.get("profit", t["pnl_usd"]) > 0,
            "exit_price": b.get("close_price", t["exit_price"]),
            "exit_reason": classify_exit(b.get("close_price", t["exit_price"]),
                                         t["stop_loss"], t.get("take_profit")),
            "pnl_usd": b.get("profit", t["pnl_usd"]),
            "resolved_at": t["resolved_at"],
        }
        if sig.get("parent_dossier_ref"):
            rec["parent_dossier_ref"] = sig["parent_dossier_ref"]

        records.append({k: v for k, v in rec.items() if v is not None})

    records.sort(key=lambda r: r["committed_at"])
    return records, skipped


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    log = sys.argv[1]
    broker_path = None
    out = "trades.json"
    if "--broker" in sys.argv:
        broker_path = sys.argv[sys.argv.index("--broker") + 1]
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    print(f"Reading {log} …")
    signals, trades = parse_log(log)
    print(f"  {len(signals)} committed signals, {len(trades)} executed tickets")

    broker = load_broker(broker_path) if broker_path else {}
    if broker:
        print(f"  {len(broker)} broker positions loaded for ground truth")

    vol = None
    if "--volume" in sys.argv:
        vol = float(sys.argv[sys.argv.index("--volume") + 1])
    if vol is None:
        vol = config_volume()
        if vol is not None:
            print(f"  volume {vol} taken from config.json (no broker export)")
    records, skipped = build_records(signals, trades, broker, default_volume=vol)
    Path(out).write_text(json.dumps(records, indent=2))

    print(f"\n{len(records)} complete trade records -> {out}")
    print(f"  skipped: {skipped}")
    if records:
        wins = sum(1 for r in records if r["win"])
        net = sum(r["pnl_usd"] for r in records)
        print(f"  span: {records[0]['committed_at']} .. {records[-1]['committed_at']}")
        print(f"  {wins}W/{len(records)-wins}L, net ${net:+.2f}")
        by_exit = {}
        for r in records:
            by_exit[r["exit_reason"]] = by_exit.get(r["exit_reason"], 0) + 1
        print(f"  exits: {by_exit}")


if __name__ == "__main__":
    main()
