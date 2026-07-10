"""
Infeasibility diagnosis — explain WHY no schedule exists.

Two techniques, both cheap and fully deterministic:

1. Static checks — arithmetic on the configuration itself (no solver):
   missing qualifications, day-availability mismatches, raw room/teacher
   capacity vs demand.

2. Relaxation probes — re-solve with one constraint group switched off
   at a time (solver.RELAXABLE). If dropping a group makes the timetable
   feasible, that group is (part of) the reason.

The result is a list of plain-language findings, shown in the UI and fed
to the AI assistant so it can discuss the conflict with the user.
"""

from __future__ import annotations

from .data import DAYS, tick_label
from .solver import solve, RELAXABLE, _day_patterns, _qualified_teachers

RELAX_LABELS = {
    "patterns":       "the fixed day patterns (Mon/Wed, Tue/Thu…) — H2",
    "young_cutoff":   "the young-learner end-time cutoff — H6",
    "travel":         "the teacher travel-gap rule — H7",
    "student_blocks": "the students' blocked time windows — H8",
    "siblings":       "the sibling shared-day rule — H9",
}


def _static_checks(school: dict) -> list[str]:
    notes: list[str] = []
    settings = school["settings"]
    tpp = settings["ticks_per_period"]

    # per-location open ticks per day
    def window_ticks(day_name: str, loc: str) -> int:
        w = school["day_hours"].get(day_name, {}).get(loc)
        return (w["close"] - w["open"]) if w else 0

    # 1. teacher qualification / availability per class
    for cls in school["classes"]:
        q = [t for t in school["teachers"] if cls["level"] in t["qualified_levels"]]
        if not q:
            notes.append(f"{cls['name']}: no teacher is qualified for level {cls['level']}.")
            continue
        patterns = _day_patterns(school, cls)
        ok_pattern = False
        for pat in patterns:
            if all(any(d in t["available_days"] for t in q) for d in pat):
                ok_pattern = True
                break
        if not ok_pattern:
            names = ", ".join(t["name"] for t in q)
            notes.append(f"{cls['name']}: qualified teachers ({names}) are not available "
                         f"on any valid day pattern for {cls['sessions_per_week']}×/week.")

    # 2. young-learner classes must physically fit before the cutoff
    cutoff = settings["young_learner_cutoff"]
    young = set(settings["young_learner_levels"])
    for cls in school["classes"]:
        if cls["level"] not in young:
            continue
        dur = cls["periods_per_session"] * tpp
        fits_somewhere = any(
            w and w["open"] + dur <= min(cutoff, w["close"])
            for locs in school["day_hours"].values() for w in locs.values()
        )
        if not fits_somewhere:
            notes.append(f"{cls['name']}: a {cls['periods_per_session']}-period session can never "
                         f"end by the young-learner cutoff ({tick_label(cutoff)}) within opening hours.")

    # 3. raw room capacity vs demand (whole week, all locations)
    demand = sum(c["periods_per_session"] * tpp * c["sessions_per_week"]
                 for c in school["classes"])
    capacity = sum(window_ticks(day, r["location"])
                   for day in DAYS for r in school["rooms"])
    if demand > capacity:
        notes.append(f"Not enough room capacity: classes need {demand // 4} room-hours/week "
                     f"but rooms offer only {capacity // 4} hours across all locations.")

    # 4. raw teacher capacity vs demand, per level
    for level in settings["levels"]:
        lvl_demand = sum(c["periods_per_session"] * tpp * c["sessions_per_week"]
                         for c in school["classes"] if c["level"] == level)
        if not lvl_demand:
            continue
        lvl_capacity = 0
        for t in school["teachers"]:
            if level not in t["qualified_levels"]:
                continue
            for d in t["available_days"]:
                lvl_capacity += max(window_ticks(DAYS[d], loc) for loc in school["locations"])
        if lvl_demand > lvl_capacity:
            notes.append(f"Level {level}: needs {lvl_demand // 4} teaching hours/week but "
                         f"qualified teachers can cover at most {lvl_capacity // 4}.")

    return notes


def diagnose(school: dict, time_limit_per_probe: int = 15) -> list[str]:
    """Call when a solve came back infeasible. Returns explanation lines."""
    notes = _static_checks(school)

    feasible_when = []
    for key in RELAXABLE:
        r = solve(school, time_limit_seconds=time_limit_per_probe, relax={key})
        if r["schedule"]:
            feasible_when.append(key)

    if feasible_when:
        for key in feasible_when:
            notes.append(f"A schedule EXISTS if you relax {RELAX_LABELS[key]} — "
                         "that rule is what makes the current configuration unsolvable.")
    else:
        r = solve(school, time_limit_seconds=time_limit_per_probe, relax=set(RELAXABLE))
        if r["schedule"]:
            notes.append("No single rule is responsible — only relaxing several rules together "
                         "yields a schedule. The configuration is over-constrained; review "
                         "teacher availability and opening hours.")
        else:
            notes.append("Even with all optional rules relaxed no schedule exists — this is a "
                         "hard capacity problem (rooms, teachers or opening hours), "
                         "not a rule conflict.")

    return notes
