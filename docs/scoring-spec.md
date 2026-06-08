# CF Challenge — Scoring & Projection Spec

> **Source of truth.** This document overrides any earlier scaffold assumptions.
> The original `scoring.py` / `win_probability.py` were built against a tier/points
> model that does **not** apply to this game.

---

## HOW THE GAME ACTUALLY WORKS

Each owner picks 4 CFB teams, each from a different conference, against a preseason
win total line (e.g., Penn State UNDER 10.5). At season's end, each pick scores a delta:

- **OVER** pick: `actual_wins − line`
- **UNDER** pick: `line − actual_wins`

Owner's final score = sum of their 4 deltas. Highest total wins.

**Constraints:**
- Each owner has exactly 4 picks
- Each owner's 4 picks must span 4 different conferences
- Multiple owners can pick the same team but must take opposite sides (one OVER, one UNDER)
- Lines can be integers or half-points (10.5, 7, 8.5)

**The hard problem:** mid-season standings are misleading because of schedule
front-loading vs back-loading. A team at 2-4 with cupcakes remaining is very different
from 2-4 with ranked opponents remaining. The platform's core value is projecting where
everyone will finish using Monte Carlo simulation of remaining games.

**Win counting:** regular season only. Bowl / CFP games do **not** count toward a team's
win total versus the line (Vegas-standard for preseason win totals).

---

## DRAFT BOARD FORMAT

Each league config uses a `picks` array (replaces the old `draft_board` / `draft_structure`):

```json
{
  "picks": [
    { "owner": "owner-1", "team": "Penn State",    "conference": "Big Ten", "side": "under", "line": 10.5 },
    { "owner": "owner-1", "team": "Florida State",  "conference": "ACC",     "side": "over",  "line": 7.0  },
    { "owner": "owner-2", "team": "Penn State",    "conference": "Big Ten", "side": "over",  "line": 10.5 }
  ]
}
```

---

## DATA SOURCE

Primary API is CollegeFootballData.com (CFBD). API key is in the `CFB_API_KEY`
environment variable (configured in GitHub Actions secrets). Endpoints used:

- `/games` — game results and full season schedules
- `/records` — team win-loss records
- `/metrics/wp/pregame` — game-level pregame win probabilities (SP+ based)
- `/lines` — betting lines per game (secondary probability source / fallback)

Authentication: Bearer token in the `Authorization` header. Free tier = 1,000
requests/month, so be efficient — pull a full season per call, not per week.

CFBD only. Do not pull from ESPN or any other source.

---

## FILE OUTPUTS

| Script | Output |
|--------|--------|
| `fetch_results.py` | `data/live_results.json`, `data/ap_rankings.json`, `data/team_records.json`, `data/pregame_wp.json`, `data/betting_lines.json` |
| `scoring.py` | `site/data/<league>/owner_standings.json` (the "current standings" snapshot) |
| `win_probability.py` | `site/data/<league>/projections.json`, `site/data/<league>/timeline.json` |
| `build_narrative_state.py` | `site/data/<league>/narrative_state.json` |
| `generate_commentary.py` | `site/data/<league>/commentary.json` |

> Note: the build list refers to the scoring output as `current_standings.json`; this
> project keeps the filename `owner_standings.json` (same schema described below) so the
> commentary engine and existing site pages keep reading one canonical file.

---

## SCORING ENGINE (scoring.py)

Simple delta scoring. For each league:

1. Load the league's `picks` array
2. Load `live_results.json` and `team_records.json`
3. For each pick, count the team's current regular-season wins
4. Calculate delta:
   - OVER: `wins_so_far − line`
   - UNDER: `line − wins_so_far`
5. Sum deltas per owner = current score
6. Sort owners by current score descending

Output → `site/data/<league>/owner_standings.json`:

```json
{
  "league_id": "league-1",
  "as_of_week": 7,
  "owners": [
    {
      "id": "owner-1",
      "name": "Z",
      "current_score": 5.5,
      "picks": [
        {
          "team": "Penn State",
          "conference": "Big Ten",
          "side": "under",
          "line": 10.5,
          "current_wins": 3,
          "current_losses": 4,
          "games_remaining": 5,
          "current_delta": 7.5
        }
      ]
    }
  ]
}
```

---

## PROJECTION ENGINE (win_probability.py)

Monte Carlo forward simulation. For each league:

1. Load picks, current results, and pregame win probabilities
2. For each of 5,000 simulation runs:
   a. Lock all completed games (wins/losses are fixed)
   b. For each remaining game involving a picked team, simulate the outcome using the
      CFBD pregame win probability (`random() < win_prob` → win)
   c. Compute projected final wins for each picked team
   d. Compute projected delta for each pick
   e. Sum deltas per owner → projected final score for this sim
   f. Track which owner has the highest score in this sim
3. After all sims, compute per owner:
   - `win_probability` = % of sims where they finished first
   - `projected_final_score`: p10, median, p90
   - Per pick: `projected_final_wins` (p10/median/p90), `projected_delta` (p10/median/p90)

If `pregame_wp.json` lacks a probability for a game, fall back to `betting_lines.json`
(convert spread → win probability), else default to 50/50.

Outputs:
- `site/data/<league>/projections.json` (current snapshot)
- `site/data/<league>/timeline.json` (append a weekly entry for the win-probability chart)

---

## NARRATIVE STATE (build_narrative_state.py)

Adapt to the delta model (no tiers):
- Track which picks are **carrying** vs **dragging** each owner
- Schedule difficulty remaining
- **Sweat meter:** how close each pick's team is to its line
- Feed this context to the commentary engine

---

## SITE FRONTEND

### index.html + app.js — Main Page
- **Scoreboard:** per owner, two numbers — current score and projected final score
  (median from sim) with a p10–p90 range bar.
- **Pick detail:** per pick — team + conference, OVER/UNDER + line, current record (W-L),
  games remaining, current delta (green/red), projected final wins (median + range),
  projected delta (green/red).
- **Schedule lookahead:** per picked team, remaining opponents with win probability and
  color-coded difficulty (red = tough, green = likely win).

### analytics.html + analytics.js — Analytics Page
- Win-probability chart stays (one line per owner over time)
- Replace tier breakdown with a per-pick delta-contribution chart
- Add projected-vs-actual score comparison
- Remove T1 dependency index → "which pick is carrying you" analysis

### bios.html — remove tier badges; show each owner's 4 picks with side + line
### teams.html — replace tier column with OVER/UNDER + line, owners/sides per team, current delta
### Sidebar leaderboard — rank, name, current score, projected score, win probability %

---

## TESTING

Use 2025 data (`SEASON=2025`; flip to 2026 when that season's data goes live). League
config picks are placeholder/TODO, so `data/test_picks.json` holds realistic 2025 picks
for end-to-end testing; `scoring.py --test` (and `win_probability.py --test`) use them.

Pipeline:
```
python scripts/fetch_results.py
python scripts/scoring.py --league all --test
python scripts/win_probability.py --league all --test
python scripts/build_narrative_state.py --league all
python -m http.server 8000 --directory site
```

---

## DO NOT TOUCH
- `.github/workflows/update-data.yml`
- `scripts/generate_commentary.py` (just needs the updated narrative state)
- `site/nav.js`
- `site/style.css` (additive styles OK; don't restructure existing ones)

## DO NOT
- Change file/folder structure beyond what this spec authorizes without asking
- Add dependencies beyond stdlib + numpy
- Pull data from any source other than CFBD
- Remove the ESPN parody theme or commentary voice definitions
