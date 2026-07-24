# Afritensor × DGML — Conformance Note (Phase 0 output)

**Pinned against:** DGML spec **v0.7**, `dgml-io/dgml-spec@33b2a57`
Reference implementation: `dgml-io/dgml@c12c84b`. Apache 2.0.
Spec status: core (§§1–10) declared settled; not yet v1.0, breaking changes
to be announced explicitly. Re-check quarterly.

**Docset:** Afritensor Trade Dossier
**Namespace:** `http://www.dgml.io/afritensor/trade-dossiers#`
**Files:** `docset.json`, `schema.rnc`, `dgml_emitter.py`

---

## The four layers — what we implement

| Layer | Status | Reasoning |
|---|---|---|
| **Semantic** | Implemented | The whole point. 32 concept elements per dossier: thesis, gates, execution, outcome, provenance. Typed values via `xsi:type` + `dg:value` (§6). |
| **Spatial** | **Not applicable — deliberately omitted** | Our records are born digital: no source PDF, no page images, so no element has a bounding box. Spec §7 Rules: elements with no source match omit `dg:origin`. Derived fields instead use the reserved `dg:origin="computed"` with `dg:itemprop`/`dg:href` to their inputs. We fabricate no coordinates. |
| **Attestation** | Phase 3 | See below — the reference CLI already anchors on NVNM Chain, which is where our receipts live. |
| **Readable** | Inherent | Self-contained XML; opens in a browser. `tools/dgml2html` in the reference impl renders it. |

## Conformance decisions made in Phase 1/2

1. **No custom attributes.** Meaning is carried by elements plus `dg:`/`xsi:`/`xml:` attributes only, matching every published sample. Gate identity lives in `xml:id` (`gate-spread`) and display text, not an invented `name=` attribute. Chain identity lives in the element name (`XeneaCommitTx`, `NvnmReceiptTx`), not a `chain=` attribute.
2. **Concept-driven naming (§4).** PascalCase `docset:` elements for anything carrying a domain concept; `dg:chunk` with `dg:structure` for scaffolding (headers, list items).
3. **Links (§8).** `Outcome` → `resolves` → `Thesis`. Re-entry dossiers use `follows` with a cross-file `dg:href`, mirroring the spec's `amends` pattern. Computed fields use `computedFrom` with semicolon-separated multi-target hrefs.
4. **No fabrication.** Unknown fields are omitted entirely rather than guessed — including provenance transactions. The first specimen legitimately carries no tx elements because the source log line records the bridge's HTTP acceptance, not the tx hash; those populate from the bridge record or by querying Xenea by `keccak(signal_id)`.

## Phase 3 finding — this changes the plan (for the better)

The reference implementation ships a **first-class CLI that anchors on NVNM
Chain directly** — the same chain our receipts already use:

```
dgml stake file <fid> --docset <ds> --chain nvnm-testnet --registry <name>
dgml stake node <fid> --docset <ds> --xpath '/dg:chunk/docset:TradeDossier/docset:Thesis'
dgml prove file|node ...
```

Consequences:

- **We do not hand-roll hashing or anchoring.** Merkle construction (RFC 6962),
  exclusive C14N element hashing, inclusion proofs, and the record format are
  all implemented upstream. We emit conformant XML and call the CLI.
- **File-level staking fits us cleanly.** A file stake hashes four slots —
  `source`, `page_image[N]`, `schema`, `dgml` — and *absent slots are omitted
  from the computation entirely*. Having no PDF and no page images is an
  explicitly supported case, giving us a 2-slot stake (`schema` + `dgml`).
- **Element-level staking maps onto commit/reveal natively.** `dgml stake node`
  anchors a single element with an inclusion proof that reveals nothing else in
  the document. Staking `<Thesis>` *before* the outcome and `<Outcome>` *after*
  expresses Zcastor's existing commit-reveal pattern in DGML's own model, with
  both proofs re-walking to the same document root.
- Requires the `chain` extra (`pip install dgml[chain]`, not yet on PyPI —
  install from the repo). Signing key lives in the OS keyring; the wallet needs
  gas on NVNM testnet before any write.

## Open question for Phase 3

The receipt's `contentHash` should bind the dossier, but the receipt tx only
exists *after* the outcome is reported — while the dossier contains that tx.
Circular. The spec's own answer: **URIs are versioned; re-anchoring the same URI
adds a record rather than overwriting** ("the chain preserves a complete audit
trail of every anchored state"). So: anchor dossier v1 at outcome time, then
emit v2 carrying the receipt tx and anchor it as the next version of the same
URI. No rewriting, full lineage.

## Finding to raise upstream (Phase 5 candidate)

Spec §3 states the docset namespace form as `http://www.dgml.io/{org}/{docset-name}#`
(with `www`, trailing `#`, kebab-case name). Every published sample uses
`http://dgml.io/{org}/{DocsetName}` (no `www`, no `#`, PascalCase). We follow the
normative spec text and will file the discrepancy — it is exactly the kind of
ambiguity the launch asked implementers to surface.

## Non-goals (unchanged)

No DGML in the signal or execution hot path. No spatial layer. No rewriting of
historical receipts. Operational `jsonl` telemetry stays as it is — DGML is the
record layer, not the log layer.
