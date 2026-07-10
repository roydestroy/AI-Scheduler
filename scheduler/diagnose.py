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
    "patterns":       "τα σταθερά μοτίβα ημερών (Δευ/Τετ, Τρί/Πέμ…) — H2",
    "young_cutoff":   "το όριο λήξης για τους μικρούς μαθητές — H6",
    "travel":         "τον κανόνα κενού μετακίνησης καθηγητών — H7",
    "student_blocks": "τις ώρες μη διαθεσιμότητας των μαθητών — H8",
    "siblings":       "τον κανόνα κοινής ημέρας αδελφών — H9",
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
            notes.append(f"{cls['name']}: κανένας καθηγητής δεν είναι καταρτισμένος "
                         f"για το επίπεδο {cls['level']}.")
            continue
        patterns = _day_patterns(school, cls)
        ok_pattern = False
        for pat in patterns:
            if all(any(d in t["available_days"] for t in q) for d in pat):
                ok_pattern = True
                break
        if not ok_pattern:
            names = ", ".join(t["name"] for t in q)
            notes.append(f"{cls['name']}: οι καταρτισμένοι καθηγητές ({names}) δεν είναι "
                         f"διαθέσιμοι σε κανένα έγκυρο μοτίβο ημερών για "
                         f"{cls['sessions_per_week']}×/εβδομάδα.")

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
            notes.append(f"{cls['name']}: μάθημα {cls['periods_per_session']} περιόδων δεν μπορεί "
                         f"ποτέ να τελειώσει έως το όριο μικρών μαθητών ({tick_label(cutoff)}) "
                         "εντός του ωραρίου λειτουργίας.")

    # 3. raw room capacity vs demand (whole week, all locations)
    demand = sum(c["periods_per_session"] * tpp * c["sessions_per_week"]
                 for c in school["classes"])
    capacity = sum(window_ticks(day, r["location"])
                   for day in DAYS for r in school["rooms"])
    if demand > capacity:
        notes.append(f"Ανεπαρκής χωρητικότητα αιθουσών: τα τμήματα χρειάζονται {demand // 4} "
                     f"ώρες-αίθουσας/εβδομάδα αλλά οι αίθουσες προσφέρουν μόνο {capacity // 4} "
                     "ώρες σε όλα τα κτήρια.")

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
            notes.append(f"Επίπεδο {level}: χρειάζεται {lvl_demand // 4} διδακτικές ώρες/εβδομάδα "
                         f"αλλά οι καταρτισμένοι καθηγητές καλύπτουν το πολύ {lvl_capacity // 4}.")

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
            notes.append(f"Πρόγραμμα ΥΠΑΡΧΕΙ αν χαλαρώσετε {RELAX_LABELS[key]} — "
                         "αυτός ο κανόνας καθιστά ανέφικτη την τρέχουσα διαμόρφωση.")
    else:
        r = solve(school, time_limit_seconds=time_limit_per_probe, relax=set(RELAXABLE))
        if r["schedule"]:
            notes.append("Δεν ευθύνεται ένας μόνο κανόνας — πρόγραμμα προκύπτει μόνο αν "
                         "χαλαρώσουν περισσότεροι κανόνες μαζί. Η διαμόρφωση είναι "
                         "υπερ-περιορισμένη· ελέγξτε τη διαθεσιμότητα καθηγητών και τα ωράρια.")
        else:
            notes.append("Ακόμη και με όλους τους προαιρετικούς κανόνες χαλαρωμένους δεν υπάρχει "
                         "πρόγραμμα — πρόκειται για πρόβλημα χωρητικότητας (αίθουσες, καθηγητές "
                         "ή ωράρια), όχι σύγκρουση κανόνων.")

    return notes
