#!/usr/bin/env python3
"""
projector.py — Board 2: Projected Finish, ratings-driven (ARCHITECTURE §3, §10.3).

Job: give each remaining game a win probability from the SP+ power-rating
differential + home field. Treat remaining games as independent trials ->
Poisson-binomial distribution over additional wins -> exact P(this pick beats
its line). Convolve a manager's picks -> projected-total distribution ->
P(win the pool). Deterministic, but clearly LABELED a projection. This is the
auto-reseeding surface: it moves only because SP+ refreshes weekly (§6).

Replaces WC's dropped sim/ + win_probability.py bracket Monte Carlo (§8 DROP).

Status: STUB — no logic yet.
"""
