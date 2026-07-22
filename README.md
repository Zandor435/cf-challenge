# CFB Fantasy Platform

Multi-group college football fantasy platform with ESPN-parody commentary, Monte Carlo win probability, and automated daily updates via GitHub Actions.

## Architecture

One codebase, three groups. The data fetch is shared (same CFB games), but scoring, standings, and commentary run per-group with separate configs.

```
fetch_results.py (shared)
       ↓
scoring.py --group group_a    →  site/data/group_a/*.json
scoring.py --group group_b    →  site/data/group_b/*.json
scoring.py --group group_c    →  site/data/group_c/*.json
       ↓
win_probability.py (per group)  →  timeline + projections
       ↓
build_narrative_state.py        →  narrative memory per group
       ↓
generate_commentary.py          →  ESPN-parody hot takes
       ↓
git add + commit + push         →  GitHub Pages auto-rebuilds
```

## Groups

| Group | Managers | Config |
|--------|--------|--------|
| Group A | 3 | `groups/group_a/config.json` |
| Group B | 5 | `groups/group_b/config.json` |
| Group C | 6 | `groups/group_c/config.json` |

## Setup

### 1. Clone and configure
```bash
git clone <your-repo-url>
cd cfb-fantasy
```

### 2. Fill in group configs
Edit each `groups/<group>/config.json`:
- Add manager names
- Set group display name
- The `picks` array gets filled after the draft (4 picks per manager, each from a
  different conference, with a direction — `O` (over) / `U` (under) — and a win-total line)

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
python scripts/scoring.py --group all --test
python scripts/win_probability.py --group all --test
python scripts/build_narrative_state.py --group all
```
Drop `--test` once each group's `picks` array is filled in. See
`docs/scoring-spec.md` for the full scoring/projection spec.

## TODO (Z fills these in)

- [ ] Manager names for all 3 groups
- [ ] Group display names / nicknames
- [ ] Sign up at collegefootballdata.com for API key
- [ ] OpenAI API key for commentary
- [ ] Create GitHub repo and push this scaffold
- [ ] Draft day: fill in the `picks` array in each group config
- [ ] Flip `SEASON` in `fetch_results.py` to 2026 once that season's data is live in CFBD
- [ ] Manager bios / fun facts for bio pages
- [ ] Manager portrait images (or use AI-generated)
