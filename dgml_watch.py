#!/usr/bin/env python3
"""
dgml_watch.py — turn completed trades into DGML dossiers as they happen.

Reads the running system's log file and writes one dossier per trade that has
reached a booked close. Trades already written are skipped, so it is safe to
run repeatedly or leave looping.

DESIGN
    Strictly read-only with respect to the trading system. It imports nothing
    from zcastor, holds no locks, and touches no state the engine relies on —
    it just reads the log the engine is already writing and produces files in
    data/dgml/. If it crashes, trading is unaffected; if it lags, it catches
    up on the next pass.

    This is the same emitter and the same extractor used for the historical
    docset, so a dossier written live is byte-identical to one rebuilt from
    the logs later. Nothing about the record depends on when it was made.

USAGE
    # one pass over whatever has completed so far
    python3 dgml_watch.py --log zcastor.log

    # keep watching, emitting new dossiers every 5 minutes
    python3 dgml_watch.py --log zcastor.log --watch 300

    # point somewhere else
    python3 dgml_watch.py --log zcastor.log --out data/dgml --volume 0.01

Ctrl-C stops a watch loop cleanly.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    from extract_trades import (build_records, config_volume, load_broker,
                                parse_log)
    from dgml_emitter import canonical_hash, emit_dossier
except ImportError as e:
    sys.exit(f"Run this from the folder holding extract_trades.py and "
             f"dgml_emitter.py ({e})")


def arg(flag: str, default=None):
    if flag in sys.argv:
        return sys.argv[sys.argv.index(flag) + 1]
    return default


def one_pass(log: str, out_dir: str, volume: float | None,
             broker_path: str | None, quiet: bool = False) -> tuple[int, int]:
    """Emit dossiers for completed trades not yet written. Returns (new, total)."""
    signals, trades = parse_log(log)
    broker = load_broker(broker_path) if broker_path else {}
    records, _ = build_records(signals, trades, broker, default_volume=volume)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    new = 0
    for rec in records:
        target = out / f"{rec['signal_id']}.dgml.xml"
        if target.exists():
            continue
        try:
            path = emit_dossier(rec, out_dir)
        except Exception as e:                      # one bad record must not
            print(f"  skipped {rec['signal_id']}: {e}")   # stop the rest
            continue
        new += 1
        if not quiet:
            r_disp = f"{rec['pnl_usd']:+.2f}"
            print(f"  + {rec['signal_id']:<28} {rec['direction']:<5} "
                  f"{rec['timeframe']:<4} {rec['session']:<11} "
                  f"{rec['exit_reason']:<12} ${r_disp:>7}  "
                  f"sha256={canonical_hash(path)[:16]}…")
    return new, len(records)


def main() -> None:
    log = arg("--log")
    if not log:
        # sensible default: the newest zcastor*.log in the working directory
        candidates = sorted(Path(".").glob("zcastor*.log"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            sys.exit("No --log given and no zcastor*.log found here.")
        log = str(candidates[0])
        print(f"Using newest log: {log}")

    out_dir = arg("--out", "data/dgml")
    broker = arg("--broker")
    volume = arg("--volume")
    volume = float(volume) if volume else config_volume()
    if volume is None and not broker:
        print("  WARNING: no volume from --volume, config.json or --broker.")
        print("  Trades without a known size will be skipped rather than guessed.")

    interval = arg("--watch")

    if not interval:
        new, total = one_pass(log, out_dir, volume, broker)
        print(f"\n{new} new dossier(s); {total} completed trades in the log.")
        print(f"Docset now holds {len(list(Path(out_dir).glob('*.dgml.xml')))} "
              f"records in {out_dir}/")
        return

    interval = int(interval)
    print(f"Watching {log} every {interval}s -> {out_dir}/   (Ctrl-C to stop)\n")
    try:
        while True:
            new, total = one_pass(log, out_dir, volume, broker, quiet=False)
            if new:
                held = len(list(Path(out_dir).glob("*.dgml.xml")))
                print(f"  [{time.strftime('%H:%M:%S')}] +{new} "
                      f"(docset now {held})\n")
            time.sleep(interval)
    except KeyboardInterrupt:
        held = len(list(Path(out_dir).glob("*.dgml.xml")))
        print(f"\nStopped. {held} dossiers in {out_dir}/")


if __name__ == "__main__":
    main()
