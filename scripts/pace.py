"""Canonical win-total pace arithmetic. No I/O, simulation, or display rounding."""
import math


def projected_final_wins(actual_wins, remaining_probabilities):
    probabilities = list(remaining_probabilities)
    if any(p is None or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("Every remaining game needs a finite probability between 0 and 1")
    return actual_wins + math.fsum(probabilities)


def team_pace(direction, final_wins, line):
    if direction not in ("O", "U"):
        raise ValueError(f"Unknown pick direction: {direction}")
    return final_wins - line if direction == "O" else line - final_wins


def manager_pace(values):
    return math.fsum(values)


def rank_key(manager):
    value = manager.get("expected_total")
    return (value is None, -(value or 0), -manager.get("floor", 0), manager["manager_id"])


def snapshot_total(manager):
    picks = manager.get("picks", [])
    values = [p.get("expected_delta") for p in picks]
    return manager_pace(values) if picks and all(v is not None for v in values) else None


def display_units(value):
    """One decimal, half away from zero; display only."""
    scaled = value * 10
    return int(scaled + (.5 if scaled >= 0 else -.5))


def format_signed(value):
    units = display_units(value)
    return ("+" if units > 0 else "-" if units < 0 else "") + f"{abs(units) / 10:.1f}"
