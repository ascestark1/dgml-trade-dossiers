# dgml-trade-dossiers

**Agentic decision records in [DGML](https://www.dgml.io) — the open document
standard founded by Docugami and Inveniam.**

Every decision made by an autonomous trading desk, written as a semantic XML
dossier: the thesis committed *before* the outcome was known, every gate verdict
that admitted or refused the trade, the execution as filled, the measured
outcome, and the on-chain anchors binding all of it.

DGML's published use cases are documents of record — leases, loans, valuation
reports: verifying what agents *read*. This is the other side of the loop:
verifying what agents *decide*.

---

## Why

An AI agent that reports its own results is asking to be trusted. These dossiers
are built so nobody has to:

- The thesis is sealed on-chain **before** the outcome exists, so it cannot be
  rewritten afterwards.
- The measured outcome is recorded against that same commitment.
- Every number in the document is typed, and every derived number carries
  `dg:origin="computed"` plus links to the exact elements it was computed from.
- The whole file opens in a browser. No decoder, no vendor API, no trust.

## Example

[`samples/sig_20260707220101_bo6x.dgml.xml`](samples/sig_20260707220101_bo6x.dgml.xml) — a real trade: a 5m
short committed at 22:01 UTC, routed to the entry watcher by the ranging-regime
gate, filled at 63,264.35, stopped out ten minutes later at −1.02R. Every value
in it reconciles to the broker's booked P&L to the cent.

```xml
<docset:RealizedR xsi:type="decimal" dg:value="-1.0180"
    dg:origin="computed" dg:itemprop="computedFrom"
    dg:href="#fill-price; #exit-price; #risk-points">-1.02R</docset:RealizedR>
```

## Usage

```bash
pip install lxml
python3 dgml_emitter.py examples/trade_bo6x.json --out out
```

Input is one JSON object (or a list) per resolved trade — see
[`examples/trade_bo6x.json`](examples/trade_bo6x.json) for the field set.
Output is one `<signal_id>.dgml.xml` per trade, plus the canonical SHA-256
that an on-chain receipt's `contentHash` should bind to.

Re-running the command above reproduces
[`samples/sig_20260707220101_bo6x.dgml.xml`](samples/) byte for byte.

## Conformance

Pinned to DGML spec **v0.7** (`dgml-io/dgml-spec@33b2a57`). See
[CONFORMANCE.md](CONFORMANCE.md) for the full note — which layers are
implemented, which are deliberately skipped, and why.

Short version:

| Layer | Status |
|---|---|
| Semantic | Implemented — 32 concept elements, typed values, semantic links |
| Spatial | Not applicable — records are born digital, so no element carries a bounding box. Derived fields use the reserved `dg:origin="computed"` instead |
| Attestation | In progress — `dgml stake node` on `<Thesis>` before the outcome and `<Outcome>` after expresses commit-reveal in DGML's native model |
| Readable | Inherent — self-contained XML |

## Design rules

1. **Offline only.** Nothing here runs in the signal or execution path. It reads
   records that already exist and writes files.
2. **No fabrication.** A field that isn't known is omitted, never guessed —
   including provenance transactions.
3. **No custom attributes.** Meaning is carried by elements plus `dg:`/`xsi:`/
   `xml:` attributes, matching every published DGML sample.

## Provenance stack

| Layer | Where |
|---|---|
| Reasoning | Private, off-chain, sealed by hash |
| Commitment + outcome | Xenea L1 (chain 1096) |
| Portable receipt | NVNM Chain (787111) |
| Readable record | DGML dossier (this repo) |

## Status

Early. The emitter is running against real trades; attestation binding is next.
Issues and corrections welcome — particularly from anyone else building agentic
decision records, since the vocabulary should be shared rather than mine.

## License

Apache 2.0, matching DGML.
