#!/usr/bin/env python3
"""
archive_split.py — take the provenance record off Xenea, into two bundles.

WHY TWO
    The archive's value is that anyone can verify it without the chain. That
    needs to be public. But SignalCommitted carries direction, confTier and
    regime for every signal — a machine-readable record of what the model
    concluded, minute by minute. It is already public on-chain, but a repo
    makes it convenient in a way an RPC never was, and if Ubusuna goes away
    the repo becomes the only place it conveniently exists.

    So one run produces two bundles from the same fetch. They cannot drift.

    PUBLIC   outcomes, receipts, heartbeats, settlements — results only.
             Exactly what the dossiers already publish, plus their proofs.
             Enough for anyone to verify a dossier without the chain.

    PRIVATE  everything, including the committed theses.

    The public manifest names the private bundle and records its hash, so the
    existence and integrity of the withheld half is provable without the half
    being disclosed. Publishing a hash is not publishing a secret.

USAGE
    python3 archive_split.py
    python3 archive_split.py --out-public pub --out-private priv
    XENEA_RPC=... python3 archive_split.py

    Run it where the chain is reachable. Commit the public bundle; keep the
    private one wherever you keep things you do not publish.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from web3 import Web3
    from eth_abi import decode as abi_decode
except ImportError:
    sys.exit("need web3 + eth_abi:  pip install web3")

RPC = os.getenv("XENEA_RPC", "https://rpc-ubusuna.xeneascan.com")
NVNM_RPC = os.getenv("NVNM_RPC", "https://evm.testnet.nvnmchain.io")
EXPLORER = "https://ubusuna.xeneascan.com"
NVNM_EXPLORER = "https://explorer.evm.testnet.nvnmchain.io"

CONTRACTS = {
    "SignalRegistry":    "0x81e27A06c92C380d780df4B82C358124c78DaE54",
    "PerformanceLedger": "0xE7374F79Cfa9648911209DF140A1fe781B8cE299",
    "AgentPayment":      "0x45901f4AfdEa5f8a5762963b1d6bF2A7862ca7b1",
}
NVNM_RECEIPTS = "0x26EDcb5778bBe058D61Be18331753d917fCbD232"

# name -> (signature, indexed names, data types, data names, VISIBILITY)
EVENTS = {
    "SignalCommitted": (
        "SignalCommitted(string,bytes32,uint8,uint8,string,string,uint256,uint256)",
        ["signalId"],
        ["bytes32", "uint8", "uint8", "string", "string", "uint256", "uint256"],
        ["hash", "direction", "confTier", "regime", "session", "entryPrice", "timestamp"],
        "private"),          # the committed thesis
    "SignalRevealed": (
        "SignalRevealed(string,string,int256,uint256,uint256)",
        ["signalId"], ["string", "int256", "uint256", "uint256"],
        ["regime", "evEst_scaled", "qualityScore", "timestamp"],
        "private"),          # model quality estimates
    "OutcomeReported": (
        "OutcomeReported(string,bool,int256,int256,uint256,uint256,uint256)",
        ["signalId"], ["bool", "int256", "int256", "uint256", "uint256", "uint256"],
        ["win", "pnlUsd_scaled", "R_scaled", "binanceClosePrice", "pythClosePrice",
         "timestamp"],
        "public"),           # results — already in the docset
    "OutcomeRecorded": (
        "OutcomeRecorded(uint8,string,bool,int256,int256,uint256,uint256,uint256)",
        ["confTier", "regime"],
        ["bool", "int256", "int256", "uint256", "uint256", "uint256"],
        ["win", "pnlUsd", "R_scaled", "binanceClosePrice", "pythClosePrice", "timestamp"],
        "public"),
    "SignalExpired": (
        "SignalExpired(string,uint256)", ["signalId"], ["uint256"], ["timestamp"],
        "public"),
    "HeartbeatPulse": (
        "HeartbeatPulse(uint256,uint256,uint256)", [],
        ["uint256", "uint256", "uint256"], ["timestamp", "totalCommits", "totalOutcomes"],
        "public"),
    "ContractsLinked": (
        "ContractsLinked(address,address)", [], ["address", "address"],
        ["agentPayment", "performanceLedger"], "public"),
    "ReceiptAttested": (
        "ReceiptAttested(string,bytes32,bool,int256,int256,uint8,bytes32,uint64,uint64)",
        ["signalId", "xeneaOutcomeTx"],
        ["bool", "int256", "int256", "uint8", "bytes32", "uint64", "uint64"],
        ["win", "R_scaled", "pnlUsd_scaled", "agentsSettled", "contentHash",
         "resolvedAt", "attestedAt"],
        "public"),           # the portable receipts
}


def topics(w3):
    out = {}
    for name, (sig, *_r) in EVENTS.items():
        h = w3.keccak(text=sig).hex()
        out[(h if h.startswith("0x") else "0x" + h).lower()] = name
    return out


def connect(rpc, label):
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 45}))
    if not w3.is_connected():
        print(f"  {label}: unreachable at {rpc}")
        return None
    print(f"  {label}: chain {w3.eth.chain_id}, head {w3.eth.block_number:,}")
    return w3


def fetch(w3, address, from_block, label):
    latest = w3.eth.block_number
    if from_block > latest:
        print(f"    {label}: start block past head — set *_FROM_BLOCK=0")
        return []
    logs, chunk, wait, start = [], 10_000, 15, from_block
    while start <= latest:
        end = min(start + chunk - 1, latest)
        try:
            logs.extend(w3.eth.get_logs({
                "address": Web3.to_checksum_address(address),
                "fromBlock": hex(start), "toBlock": hex(end)}))
            start = end + 1
        except Exception as e:
            m = str(e).lower()
            if "distance" in m or "range" in m or "limit" in m:
                if chunk <= 500:
                    raise
                chunk = max(500, chunk // 4)
            elif "429" in m or "403" in m or "too many" in m:
                print(f"    rate limited, waiting {wait}s")
                time.sleep(wait); wait = min(120, wait * 2)
            else:
                raise
    print(f"    {label}: {len(logs)} logs")
    return logs


def decode(log, tmap):
    tps = [t.hex() if hasattr(t, "hex") else str(t) for t in log["topics"]]
    tps = [t if t.startswith("0x") else "0x" + t for t in tps]
    if not tps:
        return None, None, "public"
    name = tmap.get(tps[0].lower())
    if not name:
        return None, {"raw_topics": tps}, "public"
    _sig, idx_names, dtypes, dnames, vis = EVENTS[name]
    rec = {}
    for i, nm in enumerate(idx_names, start=1):
        if i < len(tps):
            key = nm + ("_hash" if nm in ("signalId", "regime") else "")
            rec[key] = tps[i]
    try:
        data = log["data"]
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)
        for k, v in zip(dnames, abi_decode(dtypes, raw)):
            rec[k] = v.hex() if isinstance(v, (bytes, bytearray)) else v
    except Exception as e:
        rec["_decode_error"] = str(e)
    return name, rec, vis


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def manifest_for(root: Path, meta: dict) -> dict:
    files = {}
    for p in sorted(root.rglob("*.jsonl")):
        files[str(p.relative_to(root))] = {
            "sha256": sha256(p), "bytes": p.stat().st_size,
            "lines": sum(1 for _ in open(p))}
    return {**meta, "files": files}


def main():
    a = sys.argv[1:]
    pub = Path(a[a.index("--out-public") + 1] if "--out-public" in a
               else f"xenea_archive_public_{datetime.now(timezone.utc):%Y%m%d}")
    prv = Path(a[a.index("--out-private") + 1] if "--out-private" in a
               else f"xenea_archive_private_{datetime.now(timezone.utc):%Y%m%d}")

    print("Connecting:")
    xw3 = connect(RPC, "Xenea")
    nw3 = connect(NVNM_RPC, "NVNM")
    if xw3 is None:
        sys.exit("Xenea unreachable — nothing to archive")

    tmap = topics(xw3)
    for d in (pub, prv):
        (d / "raw").mkdir(parents=True, exist_ok=True)
        (d / "decoded").mkdir(parents=True, exist_ok=True)

    counts = {"public": {}, "private": {}}
    raw_total = 0
    handles = {"public": {}, "private": {}}

    sources = [(xw3, lbl, addr, int(os.getenv("XENEA_FROM_BLOCK", "0")), EXPLORER)
               for lbl, addr in CONTRACTS.items()]
    if nw3 is not None:
        sources.append((nw3, "NvnmReceipts", NVNM_RECEIPTS,
                        int(os.getenv("NVNM_FROM_BLOCK", "1690000")), NVNM_EXPLORER))

    print("\nFetching:")
    for w3, label, addr, fb, expl in sources:
        logs = fetch(w3, addr, fb, label)
        raw_total += len(logs)
        # raw goes to BOTH bundles for public events, private only otherwise
        for lg in logs:
            name, rec, vis = decode(lg, tmap)
            key = name or "UnknownEvent"
            tps = [t.hex() if hasattr(t, "hex") else str(t) for t in lg["topics"]]
            tps = [t if t.startswith("0x") else "0x" + t for t in tps]
            data = lg["data"]
            data = data if isinstance(data, str) else "0x" + bytes(data).hex()
            tx = lg["transactionHash"]
            tx = tx.hex() if hasattr(tx, "hex") else str(tx)
            tx = tx if tx.startswith("0x") else "0x" + tx
            payload = {"contract_label": label, "contract": addr,
                       "block": lg["blockNumber"], "tx": tx,
                       "explorer": f"{expl}/tx/{tx}", **(rec or {})}
            raw_line = json.dumps({"contract": addr, "block": lg["blockNumber"],
                                   "tx": tx, "topics": tps, "data": data})

            targets = ["private"] + (["public"] if vis == "public" else [])
            for t in targets:
                root = prv if t == "private" else pub
                if (t, "raw", label) not in handles[t]:
                    handles[t][(t, "raw", label)] = open(
                        root / "raw" / f"{label}.logs.jsonl", "w")
                handles[t][(t, "raw", label)].write(raw_line + "\n")
                if (t, "dec", key) not in handles[t]:
                    handles[t][(t, "dec", key)] = open(
                        root / "decoded" / f"{key}.jsonl", "w")
                handles[t][(t, "dec", key)].write(json.dumps(payload, default=str) + "\n")
                counts[t][key] = counts[t].get(key, 0) + 1

    for t in handles:
        for f in handles[t].values():
            f.close()

    common = {
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "xenea_chain_id": xw3.eth.chain_id,
        "xenea_block_range": [int(os.getenv("XENEA_FROM_BLOCK", "0")),
                              xw3.eth.block_number],
        "contracts": {**CONTRACTS, "NvnmReceipts": NVNM_RECEIPTS},
        "total_raw_logs": raw_total,
        "note": ("Indexed string parameters exist on-chain only as "
                 "keccak256(value); the plaintext was never stored by the "
                 "contract and is absent here by design. Match by hashing a "
                 "candidate signal id."),
    }

    prv_man = manifest_for(prv, {**common, "bundle": "private",
                                 "event_counts": counts["private"]})
    (prv / "manifest.json").write_text(json.dumps(prv_man, indent=2))
    prv_hash = hashlib.sha256((prv / "manifest.json").read_bytes()).hexdigest()

    pub_man = manifest_for(pub, {
        **common, "bundle": "public",
        "event_counts": counts["public"],
        "withheld": {
            "events": [n for n, v in EVENTS.items() if v[4] == "private"],
            "reason": ("committed theses and model quality estimates; already "
                       "public on-chain, not republished here"),
            "private_manifest_sha256": prv_hash,
        }})
    (pub / "manifest.json").write_text(json.dumps(pub_man, indent=2))

    print("\n" + "=" * 60)
    print(f"  PUBLIC  -> {pub}/    {sum(counts['public'].values())} events")
    for k, v in sorted(counts["public"].items(), key=lambda x: -x[1]):
        print(f"    {k:<20}{v:>6}")
    print(f"\n  PRIVATE -> {prv}/   {sum(counts['private'].values())} events")
    for k, v in sorted(counts["private"].items(), key=lambda x: -x[1]):
        print(f"    {k:<20}{v:>6}")
    print(f"\n  private manifest sha256 recorded in the public one:")
    print(f"    {prv_hash}")
    print("\n  Commit the public bundle. Keep the private one out of git.")
    print("=" * 60)


if __name__ == "__main__":
    main()
