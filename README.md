# CFB Fantasy League Platform

Multi-league college football fantasy platform with ESPN-parody commentary, Monte Carlo win probability, and automated daily updates via GitHub Actions.

## Architecture

One codebase, three leagues. The data fetch is shared (same CFB games), but scoring, standings, and commentary run per-league with separate configs.

```
fetch_results.py (shared)
       ↓
scoring.py --league league-1    →  site/data/league-1/*.json
scoring.py --league league-2    →  site/data/league-2/*.json
scoring.py --league league-3    →  site/data/league-3/*.json
       ↓
win_probability.py (per league) →  timeline + projections
       ↓
build_narrative_state.py        →  narrative memory per league
       ↓
generate_commentary.py          →  ESPN-parody hot takes
       ↓
git add + commit + push         →  GitHub Pages auto-rebuilds
```

## Leagues

| League | Owners | Config |
|--------|--------|--------|
| League 1 | 3 | `leagues/league-1/config.json` |
| League 2 | 5 | `leagues/league-2/config.json` |
| League 3 | 6 | `leagues/league-3/config.json` |

## Setup

### 1. Clone and configure
```bash
git clone <your-repo-url>
cd cfb-fantasy
```

### 2. Fill in league configs
Edit each `leagues/<league>/config.json`:
- Add owner names
- Set league display name
- The `picks` array gets filled after the draft (4 picks per owner, each from a
  different conference, with a side — `over`/`under` — and a win-total line)

### 3. Set GitHub Secrets
- `CFB_API_KEY` — your CollegeFootballData.com API key (free tier)
- `OPENAI_API_KEY` — for GPT commentary generation

### 4. Enable GitHub Pages
Settings → Pages → Source: Deploy from branch → `main` → `/site`

### 5. Local dev
```bash
python -m http.server 8000 --directory site
```

### 6. Test the pipeline
Before the draft, run against `data/test_picks.json` with `--test`:
```bash
python scripts/fetch_results.py
python scripts/scoring.py --league all --test
python scripts/win_probability.py --league all --test
python scripts/build_narrative_state.py --league all
```
Drop `--test` once each league's `picks` array is filled in. See
`docs/scoring-spec.md` for the full scoring/projection spec.

## TODO (Z fills these in)

- [ ] Owner names for all 3 leagues
- [ ] League display names / nicknames
- [ ] Sign up at collegefootballdata.com for API key
- [ ] OpenAI API key for commentary
- [ ] Create GitHub repo and push this scaffold
- [ ] Draft day: fill in the `picks` array in each league config
- [ ] Flip `SEASON` in `fetch_results.py` to 2026 once that season's data is live in CFBD
- [ ] Owner bios / fun facts for bio pages
- [ ] Owner portrait images (or use AI-generated)
