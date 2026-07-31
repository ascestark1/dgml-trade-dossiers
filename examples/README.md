# Examples

One input record per sample dossier. Each regenerates its file byte for byte:

```bash
python3 ../dgml_emitter.py trade_bo6x.json --out ../samples
```

| Input | Produces | Shows |
|---|---|---|
| `trade_bo6x.json` | `sig_20260707220101_bo6x` | a losing trade in full — four gate verdicts, stop-out at −1.02R |
| `trade_0sda.json` | `sig_20260727140301_0sda` | a clean stop-out, the control case |
| `trade_vsbr.json` | `sig_20260729003302_vsbr` | a short that reached target |
| `trade_anws.json` | `sig_20260729004602_anws` | a long that reached target |

These are the exact records the emitter consumes — nothing is hand-edited.
The field set is the contract: anything absent from the input is absent from
the dossier, because the emitter omits what it cannot source rather than
inventing it.

The whole corpus is in [`../docset/`](../docset/); these four are the annotated
ones. See [`../samples/README.md`](../samples/README.md) for what each
demonstrates and why it was chosen.
