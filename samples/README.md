# Samples

Four dossiers chosen because each shows something different. The full corpus is
in [`../docset/`](../docset/) — 231 records, every trade the desk opened and
closed between 12 June and 29 July 2026.

Read them in this order.

---

### `sig_20260707220101_bo6x.dgml.xml` — a losing trade, in full

A 5-minute short, committed 22:01 UTC, routed to the entry watcher by the
ranging-regime gate, filled at 63,264.35, stopped out ten minutes later at
−1.02R.

Start here because it is the most complete illustration of the format. Four
gate verdicts, the execution as filled, and an outcome that reconciles to the
broker's booked P&L to the cent. Nothing about it is flattering, which is the
point — a decision record that only holds winners is not a record.

Note `RealizedR`. It carries `dg:origin="computed"` and links to the three
elements it was derived from, so a reader recomputes it rather than trusting
it:

```xml
<docset:RealizedR xsi:type="decimal" dg:value="-1.0180"
    dg:origin="computed" dg:itemprop="computedFrom"
    dg:href="#fill-price; #exit-price; #risk-points">-1.02R</docset:RealizedR>
```

---

### `sig_20260727140301_0sda.dgml.xml` — a clean stop-out

A 15-minute long in the NY Open session that went to its stop and paid exactly
what the geometry said it should. Useful as the control: this is what the
system doing its job looks like when the direction is simply wrong.

---

### `sig_20260729003302_vsbr.dgml.xml` — a short that reached target

### `sig_20260729004602_anws.dgml.xml` — a long that reached target

Both from 29 July, both `take_profit`, opposite directions, forty minutes
apart.

These two matter because of what changed just before them. The watcher had
previously been filling almost immediately after a signal — a median wait of
four minutes — which meant entering while price was still moving against the
position. It now requires price to travel a multiple of recent volatility back
toward the entry before filling.

The visible consequence in these files is the `ExitReason`. Before, most
positions ended as `early_close`; these ran to their targets. Two trades prove
nothing on their own, which is why the whole corpus is here rather than a
curated selection.

---

## Reproducing them

```bash
pip install lxml
python3 ../dgml_emitter.py ../examples/trade_bo6x.json --out .
```

Every sample here has its input in [`../examples/`](../examples/), and each
regenerates its file byte for byte. The hash of
a dossier does not depend on when it was written — which is the precondition
for binding it to an on-chain receipt.

## What these files do not yet carry

The `Provenance` block holds signal identifiers, not transaction hashes.

Both chains hold the records — commits and outcomes on Xenea, receipts on
NVNM — but the emitter reads operational logs that record the bridge's
acceptance rather than the transaction hash, and it omits anything it cannot
source rather than inventing it.

Recovering those transactions by signal ID and backfilling them is the next
piece of work. Until it lands, these dossiers are internally verifiable and not
yet chain-anchored, and the files say so by omission rather than by claiming
otherwise.
