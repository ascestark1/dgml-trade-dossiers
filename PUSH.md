# One push (delete this file afterwards)

Everything in this folder is ready. From here:

```bash
cd dgml-trade-dossiers

git add -A
git commit -m "Docset to 231 records; 4 annotated samples with reproducible inputs"
git push
```

Username `ascestark1`, password is your **personal access token** (`repo` scope).

## Verify before you push

```bash
git status --short          # should list the new docset/, samples/, examples/
git check-ignore -v docset/*.xml   # should print NOTHING
```

That second command matters. If it prints anything, the docset is being
ignored and the push will silently drop 231 files.

## After

```bash
git rm PUSH.md && git commit -m "remove push notes" && git push
```

Then on the repo page, under **About**:

- Description: *Agentic decision records in DGML — verifiable AI trading decisions, anchored on-chain*
- Website: `https://www.dgml.io`
- Topics: `dgml`, `provenance`, `ai-agents`, `rwa`, `xml`, `blockchain`
