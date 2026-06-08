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
- Draft board gets filled after the draft

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
```bash
python scripts/fetch_results.py
python scripts/scoring.py --league league-1
python scripts/scoring.py --league league-2
python scripts/scoring.py --league league-3
```

## TODO (Z fills these in)

- [ ] Owner names for all 3 leagues
- [ ] League display names / nicknames
- [ ] Sign up at collegefootballdata.com for API key
- [ ] OpenAI API key for commentary
- [ ] Create GitHub repo and push this scaffold
- [ ] Draft day: fill in draft_board in each league config
- [ ] Team strength ratings (SP+ or FPI — pulled closer to season)
- [ ] Owner bios / fun facts for bio pages
- [ ] Owner portrait images (or use AI-generated)
