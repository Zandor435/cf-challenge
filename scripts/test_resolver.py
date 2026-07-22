#!/usr/bin/env python3
"""
test_resolver.py — Resolver verification (ARCHITECTURE §9). No API, no cache.

Requires BUILD 1 output (teams_canonical.json, team_aliases.json, ambiguous.json).
Asserts:
  1. INTERNAL path resolve_canonical() accepts ALL canonical strings (incl. the
     'USC'/'Miami' the human path rejects),
  2. HUMAN path resolve_team() still raises AmbiguityError on bare 'USC'/'Miami',
  3. every CURATED ambiguity candidate resolves via resolve_team,
  4. every GENERATED collision token raises + its candidates are valid canonicals,
  5. corrected human aliases resolve,
  6. the fuzzy suggester is self-correcting — 'Brigham Young' suggests 'BYU'
     even with the alias absent, and a misspelling carries suggestions.

Usage:
    python scripts/test_resolver.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (load_json, CANONICAL_PATH, resolve_team, resolve_canonical,
                   suggest_canonical, load_ambiguous, AMBIGUOUS_BASE,
                   AmbiguityError, UnknownTeamError)

_res = []


def check(name, ok, detail=""):
    _res.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main():
    teams = [t["school"] for t in load_json(CANONICAL_PATH)["teams"]]

    print(f"[1] internal path accepts all {len(teams)} canonical strings")
    bad = []
    for s in teams:
        try:
            if resolve_canonical(s) != s:
                bad.append((s, resolve_canonical(s)))
        except Exception as e:
            bad.append((s, f"RAISED {type(e).__name__}"))
    check(f"all {len(teams)} pass resolve_canonical()", not bad, f"failures={bad[:5]}")
    for s in ("USC", "Miami"):
        check(f"resolve_canonical({s!r}) == {s!r}", s in teams and resolve_canonical(s) == s)

    print("[2] human path keeps the ambiguity guard on USC/Miami")
    for t in ("USC", "Miami"):
        try:
            resolve_team(t)
            check(f"resolve_team({t!r}) raises", False)
        except AmbiguityError:
            check(f"resolve_team({t!r}) raises AmbiguityError", True)
        except Exception as e:
            check(f"resolve_team({t!r}) raises AmbiguityError", False, str(e))

    print("[3] curated ambiguity candidates all resolve via resolve_team")
    for token, cands in AMBIGUOUS_BASE.items():
        for c in cands:
            try:
                check(f"base candidate {c!r} resolves", True, f"-> {resolve_team(c)}")
            except Exception as e:
                check(f"base candidate {c!r} resolves", False, str(e))

    print("[4] generated collisions: token raises + candidates are valid canonicals")
    amb = load_ambiguous()
    gen_tokens = [k for k in amb if k not in AMBIGUOUS_BASE]
    check("generated collisions exist", len(gen_tokens) > 0, f"{len(gen_tokens)} tokens")
    problems = []
    for k in gen_tokens:
        try:
            resolve_team(k)
            problems.append((k, "did NOT raise"))
        except AmbiguityError:
            pass
        except Exception as e:
            problems.append((k, f"non-ambiguous {type(e).__name__}"))
        for c in amb[k]:
            try:
                resolve_canonical(c)
            except Exception:
                problems.append((k, f"invalid candidate {c!r}"))
    check("every generated token raises + candidates valid", not problems, f"{problems[:5]}")

    print("[4b] State-school acronyms: XSU forms raise with the right candidates")
    # "State" -> "SU": the forms people actually type (OSU/MSU/ASU/...), not the
    # bare "OS"/"MS" nobody types. These six are genuinely ambiguous and must raise.
    state_amb = {
        "OSU": {"Ohio State", "Oklahoma State", "Oregon State"},
        "MSU": {"Michigan State", "Mississippi State", "Missouri State"},
        "ASU": {"App State", "Arizona State", "Arkansas State"},
        "FSU": {"Florida State", "Fresno State"},
        "KSU": {"Kansas State", "Kennesaw State", "Kent State"},
        "BSU": {"Ball State", "Boise State"},
    }
    for tok, expected in state_amb.items():
        try:
            resolve_team(tok)
            check(f"{tok} raises ambiguity", False, "did NOT raise")
        except AmbiguityError as e:
            check(f"{tok} raises ambiguity with correct candidates",
                  set(e.candidates) == expected,
                  f"got {sorted(e.candidates)}")
        except Exception as e:
            check(f"{tok} raises ambiguity", False, f"{type(e).__name__}: {e}")
    try:
        got = resolve_team("PSU")   # unique State school -> resolves cleanly
        check("PSU resolves cleanly to Penn State", got == "Penn State", f"got {got!r}")
    except Exception as e:
        check("PSU resolves cleanly to Penn State", False, f"{type(e).__name__}: {e}")

    print("[5] corrected human aliases resolve")
    cases = {
        "Mississippi": "Ole Miss", "Appalachian State": "App State",
        "UMass": "Massachusetts", "Louisiana Monroe": "UL Monroe",
        "Connecticut": "UConn", "Brigham Young": "BYU",
        "Middle Tennessee State": "Middle Tennessee", "San Jose State": "San José State",
    }
    for name, exp in cases.items():
        try:
            got = resolve_team(name)
            check(f"resolve_team({name!r}) -> {exp!r}", got == exp, f"got {got!r}")
        except Exception as e:
            check(f"resolve_team({name!r}) -> {exp!r}", False, str(e))

    print("[6] fuzzy suggester is self-correcting")
    sugg = suggest_canonical("Brigham Young")
    check("suggest_canonical('Brigham Young') includes 'BYU'", "BYU" in sugg, f"got {sugg}")
    try:
        resolve_team("Ohio Stat")  # typo, no alias
        check("misspelling raises UnknownTeamError with suggestions", False)
    except UnknownTeamError as e:
        check("misspelling raises UnknownTeamError with suggestions",
              bool(e.suggestions), f"{e.suggestions}")
    except Exception as e:
        check("misspelling raises UnknownTeamError with suggestions", False, str(e))

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
