#!/usr/bin/env python3
"""
backfill_anchors.py — put the on-chain transactions into the dossiers.

THE GAP
    Every dossier carries signal identifiers but no transaction hashes. Both
    chains hold the records; the emitter reads operational logs that captured
    the bridge's HTTP acceptance rather than the tx hash, and it omits what it
    cannot source rather than inventing it.

    So the Provenance block currently says which signal, not which transaction.
    This closes that.

HOW
    Indexed string parameters are stored on-chain only as keccak256(value), so
    a signal id cannot be read back out of a log — but it CAN be matched. This
    fetches every relevant event once, indexes them by that hash, then matches
    all dossiers locally. Three bulk fetches instead of ~700 point lookups
    against rate-limited public RPCs.

        Xenea  SignalCommitted   -> XeneaCommitTx
        Xenea  OutcomeReported   -> XeneaOutcomeTx
        NVNM   ReceiptAttested   -> NvnmReceiptTx

    Both the signal id and the watched-entry id are tried, since a trade may
    be committed under either.

HONESTY
    Trades whose bridge commit timed out have no transaction to find. Those
    dossiers stay as they are and the run reports exactly how many. A partial
    result is the correct outcome, not a failure.

USAGE
    python3 backfill_anchors.py --trades trades.json --out trades_anchored.json
    python3 dgml_emitter.py trades_anchored.json --out docset

    XENEA_RPC=... NVNM_RPC=... python3 backfill_anchors.py ...
    python3 backfill_anchors.py --dry-run     # report coverage, write nothing
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    from web3 import Web3
except ImportError:
    sys.exit("need web3:  pip install web3")

XENEA_RPC = os.getenv("XENEA_RPC", "https://rpc-ubusuna.xeneascan.com")
NVNM_RPC  = os.getenv("NVNM_RPC",  "https://evm.testnet.nvnmchain.io")

SIGNAL_REGISTRY = "0x81e27A06c92C380d780df4B82C358124c78DaE54"
NVNM_RECEIPTS   = "0x26EDcb5778bBe058D61Be18331753d917fCbD232"

# Verified byte-for-byte against the live explorers before this was written.
TOPIC = {
    "SignalCommitted": "0xbecd35a49d5ac4a745a4929227d1836e223fcdea6049fd04420c0089f030a1b1",
    "OutcomeReported": "0xd7bc90a7b314cbf2954c472d1c018339fab1bb78c0782cf7338a60eb17899fe5",
    "ReceiptAttested": "0xb0a56ebcd86fad10d25a3568f6e1a86ae62eed11580f03bbe37d8db28142fe16",
}

# The registry deployed well after genesis; scanning from 0 wastes hundreds of
# empty calls. Override if a chain is re-indexed.
FROM_BLOCK = {
    "xenea": int(os.getenv("XENEA_FROM_BLOCK", "0")),
    "nvnm":  int(os.getenv("NVNM_FROM_BLOCK", "1690000")),
}


def connect(rpc: str, label: str):
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 45}))
    if not w3.is_connected():
        print(f"  cannot reach {label} at {rpc}")
        return None
    print(f"  {label}: connected, chain {w3.eth.chain_id}, head {w3.eth.block_number:,}")
    return w3


def fetch_logs(w3, address, topic0, from_block, label):
    """All logs for one event, adaptive chunking, polite under rate limits."""
    latest = w3.eth.block_number
    if from_block > latest:
        print(f"    {label}: SKIPPED — start block {from_block:,} is past the "
              f"chain head {latest:,}.")
        print(f"      The chain may have been re-indexed. Re-run with "
              f"XENEA_FROM_BLOCK=0 / NVNM_FROM_BLOCK=0 to scan from genesis.")
        return []
    chunk = 10_000
    wait = 15
    out = []
    start = from_block
    while start <= latest:
        end = min(start + chunk - 1, latest)
        try:
            got = w3.eth.get_logs({
                "address": Web3.to_checksum_address(address),
                "topics": [topic0],
                "fromBlock": hex(start), "toBlock": hex(end),
            })
            out.extend(got)
            start = end + 1
            if out and (start - from_block) % (chunk * 20) < chunk:
                print(f"    {label}: block {end:,}/{latest:,}, {len(out)} events")
        except Exception as e:
            m = str(e).lower()
            if "distance" in m or "range" in m or "limit" in m:
                if chunk <= 500:
                    raise
                chunk = max(500, chunk // 4)
                print(f"    range capped, chunk -> {chunk}")
            elif "429" in m or "403" in m or "too many" in m:
                print(f"    rate limited, waiting {wait}s")
                time.sleep(wait)
                wait = min(120, wait * 2)
            else:
                raise
    print(f"    {label}: {len(out)} events total")
    return out


def index_by_signal(logs):
    """topic[1] is keccak(signalId) -> transaction hash."""
    out = {}
    for lg in logs:
        topics = lg["topics"]
        if len(topics) < 2:
            continue
        key = topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])
        key = (key if key.startswith("0x") else "0x" + key).lower()
        tx = lg["transactionHash"]
        tx = tx.hex() if hasattr(tx, "hex") else str(tx)
        out[key] = tx if tx.startswith("0x") else "0x" + tx
    return out


def khash(w3, s: str) -> str:
    h = w3.keccak(text=s).hex()
    return (h if h.startswith("0x") else "0x" + h).lower()


def main():
    args = sys.argv[1:]
    src = Path(args[args.index("--trades") + 1] if "--trades" in args else "trades.json")
    dst = Path(args[args.index("--out") + 1] if "--out" in args else "trades_anchored.json")
    dry = "--dry-run" in args

    if not src.exists():
        sys.exit(f"{src} not found")
    trades = json.loads(src.read_text())
    print(f"{len(trades)} trade records loaded from {src}\n")

    print("Connecting:")
    xw3 = connect(XENEA_RPC, "Xenea")
    nw3 = connect(NVNM_RPC, "NVNM")
    if xw3 is None and nw3 is None:
        sys.exit("\nneither chain reachable — nothing to do")

    commits = outcomes = receipts = {}
    if xw3 is not None:
        print("\nFetching Xenea events:")
        commits = index_by_signal(fetch_logs(
            xw3, SIGNAL_REGISTRY, TOPIC["SignalCommitted"],
            FROM_BLOCK["xenea"], "SignalCommitted"))
        outcomes = index_by_signal(fetch_logs(
            xw3, SIGNAL_REGISTRY, TOPIC["OutcomeReported"],
            FROM_BLOCK["xenea"], "OutcomeReported"))
    if nw3 is not None:
        print("\nFetching NVNM events:")
        receipts = index_by_signal(fetch_logs(
            nw3, NVNM_RECEIPTS, TOPIC["ReceiptAttested"],
            FROM_BLOCK["nvnm"], "ReceiptAttested"))

    hasher = xw3 or nw3
    print(f"\nIndexed: {len(commits)} commits, {len(outcomes)} outcomes, "
          f"{len(receipts)} receipts\n")

    stats = {"commit": 0, "outcome": 0, "receipt": 0, "none": 0}
    for t in trades:
        ids = [i for i in (t.get("signal_id"), t.get("entry_signal_id")) if i]
        keys = [khash(hasher, i) for i in ids]
        found = False
        for tag, table, field in (("commit", commits, "xenea_commit_tx"),
                                  ("outcome", outcomes, "xenea_outcome_tx"),
                                  ("receipt", receipts, "nvnm_receipt_tx")):
            for k in keys:
                if k in table:
                    t[field] = table[k]
                    stats[tag] += 1
                    found = True
                    break
        if not found:
            stats["none"] += 1

    n = len(trades)
    print("=" * 58)
    print("  COVERAGE")
    print("=" * 58)
    for tag, field in (("commit", "XeneaCommitTx"), ("outcome", "XeneaOutcomeTx"),
                       ("receipt", "NvnmReceiptTx")):
        print(f"  {field:<18}{stats[tag]:>5}/{n}  ({stats[tag]/n*100:>5.1f}%)")
    print(f"\n  no anchor found at all: {stats['none']}/{n}")
    if stats["none"]:
        print("  (bridge commits that timed out have no transaction to find —")
        print("   those dossiers stay as they are, which is the honest result)")

    if dry:
        print("\n  --dry-run: nothing written")
        return
    dst.write_text(json.dumps(trades, indent=2) + "\n")
    print(f"\n  wrote {dst}")
    print(f"  next:  python3 dgml_emitter.py {dst} --out docset")


if __name__ == "__main__":
    main()
