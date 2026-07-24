# Pushing this repo (delete this file after the first push)

## 1. Create the empty repo on GitHub

github.com → **New repository**

- Owner: `ascestark1`
- Name: `dgml-trade-dossiers`
- Public
- **Do NOT** tick "Add a README", "Add .gitignore", or "Choose a license" —
  all three are already in this folder, and ticking them creates a conflicting
  first commit you'd have to merge.

## 2. Push from this folder

```bash
cd dgml-trade-dossiers

git init
git add .
git commit -m "Agentic decision records in DGML: emitter, docset vocabulary, first dossier"
git branch -M main
git remote add origin https://github.com/ascestark1/dgml-trade-dossiers.git
git push -u origin main
```

When git asks for credentials:

- **Username:** `ascestark1`
- **Password:** paste your **personal access token** (not your account password —
  GitHub stopped accepting those for git over HTTPS)

The token needs only the `repo` scope. Create one at
Settings → Developer settings → Personal access tokens → Fine-grained tokens,
scoped to this single repository if you want to be strict.

### If you'd rather not retype the token on every push

```bash
git config --global credential.helper store
```

This writes the token in plaintext to `~/.git-credentials`. Fine on your own
machine, never on a shared one. Alternative that keeps it out of a file:

```bash
git remote set-url origin git@github.com:ascestark1/dgml-trade-dossiers.git
```

and use an SSH key instead.

## 3. Sanity check before you push

```bash
pip install lxml
python3 dgml_emitter.py examples/trade_bo6x.json --out out
diff out/sig_20260707220101_bo6x.dgml.xml samples/sig_20260707220101_bo6x.dgml.xml && echo "reproducible ✓"
rm -rf out
```

If the diff is clean, anyone cloning the repo can regenerate the sample from
the input — which is the point.

## 4. After the push

- **Delete this file** (`git rm PUSH.md && git commit -m "remove push notes" && git push`)
- Add a short repo description on GitHub:
  *Agentic decision records in DGML — verifiable AI trading decisions, anchored on-chain*
- Add topics: `dgml`, `provenance`, `ai-agents`, `rwa`, `xml`, `blockchain`
- Then the three posts on `dgml-io/dgml-spec`:
  1. **Discussion** — the agentic-decision-records use case, linking this repo
  2. **Issue** — the namespace discrepancy (spec §3 says
     `http://www.dgml.io/{org}/{docset-name}#`; every published sample uses
     `http://dgml.io/{org}/{DocsetName}`)
  3. **Issue** — proposing the trade dossier for their `samples/` folder
     (their CONTRIBUTING.md asks contributors to open an Issue to discuss a
     new real-world document type first)

A CLA is only triggered when you open a pull request. Discussions and Issues
need nothing.
