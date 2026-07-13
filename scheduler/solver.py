"""
Language School Scheduler — CP-SAT Solver v3
=============================================
Same model as v2, but fully parametric: `solve(school)` takes the whole
configuration as a JSON-serialisable dict (see data.default_school for
the shape), so the engine can be driven by the CLI, the REST API, or a
file on disk.

Time model
----------
  • 15-min ticks from midnight.  tick(h,m) = h*4 + m//15
  • 1 period = 4 ticks = 60 min  (50 min teaching + 10 min break)
  • A session of N periods occupies ticks [start, start+N*4) in one room.

Decision variables
------------------
  One (BoolVar active, IntVar start, optional IntervalVar) per valid
  (class, session index, day, room, teacher) combination.

Hard constraints
----------------
  H1  Each (class, session) has exactly one active assignment.
  H2  Sessions of a class fall on the required day pattern
      (Mon/Wed or Tue/Thu for 2×; triplets for 3×).
  H3  No room overlap.
  H4  No teacher overlap.
  H5  Session fits within the location's operating window on that day.
  H6  Young-learner classes end by the configured cutoff.
  H7  Teacher travel: switching branch mid-day needs a gap of
      travel_periods free periods.
  H8  Student blocked windows are respected.
  H9  Sibling groups share at least one day per week.

Soft constraints (objective)
-----------------------------
  S1  Prefer sessions at preferred_location (penalty 3 per violation).
  S2  Penalise Friday sessions (penalty 2 each).
"""

from __future__ import annotations
from ortools.sat.python import cp_model
from collections import defaultdict
from itertools import combinations

from .data import DAYS


# ── helpers ───────────────────────────────────────────────────────────────────

def _valid_start_ticks(school: dict, day: int, location: str, duration_ticks: int) -> list[int]:
    """All 15-min-aligned start ticks where a session fits inside the window."""
    w = school["day_hours"].get(DAYS[day], {}).get(location)
    if not w:
        return []
    return list(range(w["open"], w["close"] - duration_ticks + 1))

def _qualified_teachers(school: dict, cls: dict) -> list[str]:
    return [t["id"] for t in school["teachers"] if cls["level"] in t["qualified_levels"]]

def _class_blocked_windows(school: dict, cls_id: str) -> list:
    """Union of all student blocked windows for this class."""
    windows = []
    for s in school["students"]:
        if s["class_id"] == cls_id:
            windows.extend(s["blocked_windows"])
    return windows

def _overlaps(start1: int, dur1: int, start2: int, dur2: int) -> bool:
    return start1 < start2 + dur2 and start2 < start1 + dur1

def _sibling_pairs(school: dict) -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for s in school["students"]:
        if s["sibling_group"]:
            groups[s["sibling_group"]].append(s["class_id"])
    pairs = []
    for cls_list in groups.values():
        cls_list = list(set(cls_list))
        for a, b in combinations(cls_list, 2):
            pairs.append((a, b))
    return pairs

def _open_days(school: dict) -> list[int]:
    return sorted({DAYS.index(d) for d, locs in school["day_hours"].items()
                   if any(locs.values())})

def _day_patterns(school: dict, cls: dict) -> list[tuple]:
    sessions_per_week = cls["sessions_per_week"]
    if sessions_per_week == 1:
        # any single teaching day
        return [(d,) for d in _open_days(school)]
    if sessions_per_week == 2:
        patterns = [tuple(p) for p in school["settings"]["day_pairs_2x"]]
        if cls.get("saturday_preferred"):
            # weekend-friendly classes may also pair a weekday with Saturday
            patterns += [(d, 5) for d in (0, 1, 2, 3)]
        return patterns
    if sessions_per_week == 3:
        patterns = [tuple(p) for p in school["settings"]["day_triplets_3x"]]
        if cls.get("saturday_preferred"):
            patterns += [(0, 2, 5), (1, 3, 5)]
        return patterns
    raise ValueError(f"Unsupported sessions_per_week={sessions_per_week}")


# ── main solver ───────────────────────────────────────────────────────────────

#: constraint groups that can be switched off for infeasibility diagnosis
RELAXABLE = ("patterns", "young_cutoff", "travel", "student_blocks", "siblings")


def solve(school: dict, time_limit_seconds: int = 120,
          relax: frozenset[str] | set[str] = frozenset()) -> dict:
    """Build and solve the timetable. `relax` names constraint groups
    (see RELAXABLE) to skip — used by scheduler.diagnose to explain
    infeasible configurations."""
    model  = cp_model.CpModel()
    solver = cp_model.CpSolver()

    settings    = school["settings"]
    ticks_per_period = settings["ticks_per_period"]
    young_levels     = set(settings["young_learner_levels"])
    young_cutoff     = settings["young_learner_cutoff"]

    class_map   = {c["id"]: c for c in school["classes"]}
    class_sizes: dict[str, int] = {}
    for st_ in school["students"]:
        class_sizes[st_["class_id"]] = class_sizes.get(st_["class_id"], 0) + 1
    room_map    = {r["id"]: r for r in school["rooms"]}
    teacher_map = {t["id"]: t for t in school["teachers"]}

    # ── enumerate candidate (class, sess_idx, day, room, teacher, start_tick) -
    # Key:   (c_id, sess_idx, day, room_id, teacher_id)
    # Value: {"active": BoolVar, "start": IntVar, "interval": IntervalVar, ...}
    candidates: dict[tuple, dict] = {}
    warnings: list[str] = []

    for cls in school["classes"]:
        c         = cls["id"]
        n_sess    = cls["sessions_per_week"]
        dur       = cls["periods_per_session"] * ticks_per_period
        blocked   = [] if "student_blocks" in relax else _class_blocked_windows(school, c)
        q_teachers= _qualified_teachers(school, cls)
        patterns  = _day_patterns(school, cls)

        # pinned teacher: hard-assign this class to one teacher (set by
        # dragging a teacher onto the class in the schedule UI)
        pin = cls.get("pinned_teacher")
        if pin:
            if pin in q_teachers:
                q_teachers = [pin]
            else:
                tname = teacher_map.get(pin, {}).get("name", pin)
                warnings.append(f"Το {cls['name']}: ο καρφιτσωμένος καθηγητής {tname} δεν είναι "
                                f"καταρτισμένος για το επίπεδο {cls['level']} — η καρφίτσα αγνοείται.")

        if not q_teachers:
            warnings.append(f"Δεν υπάρχει καταρτισμένος καθηγητής για το {cls['name']} "
                            f"(επίπεδο {cls['level']}).")

        for sess_idx in range(n_sess):
            # Which days could this session index land on?
            if "patterns" in relax:
                days_for_sess = set(_open_days(school))
            else:
                days_for_sess = set(pat[sess_idx] for pat in patterns)

            size = class_sizes.get(c, 0)
            for day in days_for_sess:
                for room in school["rooms"]:
                    cap = room.get("capacity")
                    if cap and size > cap:      # room too small for this class
                        continue
                    loc = room["location"]
                    starts = _valid_start_ticks(school, day, loc, dur)
                    if not starts:
                        continue
                    for t_id in q_teachers:
                        tchr = teacher_map[t_id]
                        if day not in tchr["available_days"]:
                            continue

                        # Filter starts by student + teacher blocked windows
                        all_blocks = list(blocked) + list(tchr.get("blocked_windows", []))
                        valid_starts = []
                        for st in starts:
                            hit = False
                            for (bday, bopen, bclose) in all_blocks:
                                if bday == day and _overlaps(st, dur, bopen, bclose - bopen):
                                    hit = True
                                    break
                            if not hit:
                                valid_starts.append(st)

                        if not valid_starts:
                            continue

                        key = (c, sess_idx, day, room["id"], t_id)
                        active = model.new_bool_var(f"act_{'_'.join(str(x) for x in key)}")
                        start  = model.new_int_var_from_domain(
                            cp_model.Domain.from_values(valid_starts),
                            f"st_{'_'.join(str(x) for x in key)}"
                        )
                        interval = model.new_optional_interval_var(
                            start, dur, start + dur, active,
                            f"iv_{'_'.join(str(x) for x in key)}"
                        )
                        candidates[key] = {
                            "active":   active,
                            "start":    start,
                            "interval": interval,
                            "duration": dur,
                            "day":      day,
                            "room":     room["id"],
                            "teacher":  t_id,
                            "class":    c,
                            "sess_idx": sess_idx,
                        }

    # ── classes with a session that has no candidate at all can never be
    # scheduled; exclude them (with a warning) instead of making the whole
    # school infeasible — common right after an ERP import, before teachers
    # have been qualified for the new levels.
    excluded: set[str] = set()
    for cls in school["classes"]:
        c = cls["id"]
        for sess_idx in range(cls["sessions_per_week"]):
            if not any(k[0] == c and k[1] == sess_idx for k in candidates):
                excluded.add(c)
                size = class_sizes.get(c, 0)
                caps = [r.get("capacity") for r in school["rooms"]]
                if size and caps and all(cap and size > cap for cap in caps):
                    warnings.append(f"Το {cls['name']} δεν μπόρεσε να προγραμματιστεί: έχει "
                                    f"{size} μαθητές αλλά η μεγαλύτερη αίθουσα χωρά μόνο "
                                    f"{max(cap for cap in caps if cap)}.")
                else:
                    warnings.append(f"Το {cls['name']} δεν μπόρεσε να προγραμματιστεί καθόλου — "
                                    "δεν υπάρχει έγκυρος συνδυασμός καθηγητή/αίθουσας/ώρας. "
                                    "Ελέγξτε τα προσόντα και τη διαθεσιμότητα των καθηγητών, "
                                    "τα ωράρια λειτουργίας και τις χωρητικότητες αιθουσών.")
                break
    for k, v in candidates.items():
        if k[0] in excluded:
            model.add(v["active"] == 0)

    # ── H1: each (class, session) has exactly one active assignment ───────────
    for cls in school["classes"]:
        c = cls["id"]
        if c in excluded:
            continue
        for sess_idx in range(cls["sessions_per_week"]):
            sess_vars = [v["active"] for k, v in candidates.items()
                         if k[0] == c and k[1] == sess_idx]
            model.add(sum(sess_vars) == 1)

    # ── H2: sessions of a class must follow a valid day pattern ──────────────
    if "patterns" in relax:
        # relaxed: only require the sessions of a class on distinct days
        for cls in school["classes"]:
            c = cls["id"]
            for day in range(len(DAYS)):
                on_day = [v["active"] for k, v in candidates.items()
                          if k[0] == c and v["day"] == day]
                if len(on_day) > 1:
                    model.add(sum(on_day) <= 1)
    for cls in (school["classes"] if "patterns" not in relax else []):
        c        = cls["id"]
        if c in excluded:
            continue
        patterns = _day_patterns(school, cls)

        pattern_bools = []
        for pat_idx, pattern in enumerate(patterns):
            pat_bool = model.new_bool_var(f"pat_{c}_{pat_idx}")
            pattern_bools.append(pat_bool)
            # If this pattern is chosen, session i must be on pattern[i]
            for sess_idx, required_day in enumerate(pattern):
                on_day = [v["active"] for k, v in candidates.items()
                          if k[0] == c and k[1] == sess_idx and k[2] == required_day]
                if on_day:
                    model.add(sum(on_day) == 1).only_enforce_if(pat_bool)
                else:
                    model.add(pat_bool == 0)
                off_day = [v["active"] for k, v in candidates.items()
                           if k[0] == c and k[1] == sess_idx and k[2] != required_day]
                for ov in off_day:
                    model.add(ov == 0).only_enforce_if(pat_bool)

        model.add(sum(pattern_bools) == 1)

    # ── H3: no room overlap on same day ───────────────────────────────────────
    by_room_day: dict[tuple, list] = defaultdict(list)
    for v in candidates.values():
        by_room_day[(v["room"], v["day"])].append(v["interval"])
    for intervals in by_room_day.values():
        if len(intervals) > 1:
            model.add_no_overlap(intervals)

    # ── H4: no teacher overlap on same day ────────────────────────────────────
    by_teacher_day: dict[tuple, list] = defaultdict(list)
    for v in candidates.values():
        by_teacher_day[(v["teacher"], v["day"])].append(v["interval"])
    for intervals in by_teacher_day.values():
        if len(intervals) > 1:
            model.add_no_overlap(intervals)

    # ── H5: session fits in window — already enforced by _valid_start_ticks ───
    # H6: young-learner end time ───────────────────────────────────────────────
    for k, v in (candidates.items() if "young_cutoff" not in relax else []):
        cls = class_map[k[0]]
        if cls["level"] in young_levels:
            model.add(v["start"] + v["duration"] <= young_cutoff).only_enforce_if(v["active"])

    # ── H7: teacher travel time between locations on same day ─────────────────
    travel_ticks = settings["travel_periods"] * ticks_per_period
    by_td: dict[tuple, list] = defaultdict(list)
    if "travel" not in relax:
        for k, v in candidates.items():
            by_td[(v["teacher"], v["day"])].append(v)

    for (t_id, day), sess_list in by_td.items():
        if len(sess_list) < 2:
            continue
        for i, a in enumerate(sess_list):
            for b in sess_list[i+1:]:
                loc_a = room_map[a["room"]]["location"]
                loc_b = room_map[b["room"]]["location"]
                if loc_a == loc_b:
                    continue
                both_active = model.new_bool_var(
                    f"both_{t_id}_{day}_{id(a)}_{id(b)}")
                model.add_bool_and([a["active"], b["active"]]).only_enforce_if(both_active)
                model.add_bool_or([a["active"].negated(), b["active"].negated()]).only_enforce_if(both_active.negated())

                a_before_b = model.new_bool_var(f"ab_{id(a)}_{id(b)}")
                model.add(b["start"] >= a["start"] + a["duration"] + travel_ticks).only_enforce_if([both_active, a_before_b])
                model.add(a["start"] >= b["start"] + b["duration"] + travel_ticks).only_enforce_if([both_active, a_before_b.negated()])

    # ── H8: student blocks — already filtered in candidate generation ─────────
    # H9: sibling groups ───────────────────────────────────────────────────────
    sibling_pairs = [] if "siblings" in relax else [
        (a, b) for (a, b) in _sibling_pairs(school)
        if a not in excluded and b not in excluded
    ]
    for (c1, c2) in sibling_pairs:
        day_overlap_bools = []
        for day in range(len(DAYS)):
            c1_on_day = [v["active"] for k, v in candidates.items()
                         if k[0] == c1 and v["day"] == day]
            c2_on_day = [v["active"] for k, v in candidates.items()
                         if k[0] == c2 and v["day"] == day]
            if not c1_on_day or not c2_on_day:
                continue
            c1_b = model.new_bool_var(f"c1d_{c1}_{day}")
            c2_b = model.new_bool_var(f"c2d_{c2}_{day}")
            model.add(sum(c1_on_day) >= 1).only_enforce_if(c1_b)
            model.add(sum(c1_on_day) == 0).only_enforce_if(c1_b.negated())
            model.add(sum(c2_on_day) >= 1).only_enforce_if(c2_b)
            model.add(sum(c2_on_day) == 0).only_enforce_if(c2_b.negated())
            both = model.new_bool_var(f"sib_{c1}_{c2}_d{day}")
            model.add_bool_and([c1_b, c2_b]).only_enforce_if(both)
            model.add_bool_or([c1_b.negated(), c2_b.negated()]).only_enforce_if(both.negated())
            day_overlap_bools.append(both)
        if day_overlap_bools:
            model.add_bool_or(day_overlap_bools)

    # ── H10: a student in several classes must not have overlapping sessions ──
    # (e.g. an English class + a study-lab group). Only students enrolled in
    # 2+ schedulable classes matter.
    stud_classes: dict[str, set] = defaultdict(set)
    for s in school["students"]:
        if s["class_id"] not in excluded:
            stud_classes[s["id"]].add(s["class_id"])
    for sid, cids in stud_classes.items():
        if len(cids) < 2:
            continue
        by_day_s: dict[int, list] = defaultdict(list)
        for k, v in candidates.items():
            if k[0] in cids:
                by_day_s[v["day"]].append(v["interval"])
        for intervals in by_day_s.values():
            if len(intervals) > 1:
                model.add_no_overlap(intervals)

    # ── Time pins: force a session of the class at exactly (day, start) ───────
    # Set by dragging a class block onto a slot in the schedule UI.
    for cls in school["classes"]:
        c = cls["id"]
        if c in excluded:
            continue
        for pin in cls.get("pinned_slots", []):
            pd, ps = pin.get("day"), pin.get("start")
            eqs = []
            for k, v in candidates.items():
                if k[0] == c and v["day"] == pd:
                    eq = model.new_bool_var(f"tpin_{c}_{pd}_{ps}_{id(v)}")
                    model.add(v["start"] == ps).only_enforce_if(eq)
                    model.add_implication(eq, v["active"])
                    eqs.append(eq)
            if eqs:
                model.add_bool_or(eqs)
            else:
                from .data import tick_label as _tl
                warnings.append(f"Το {cls['name']}: το καρφιτσωμένο σημείο "
                                f"{DAYS[pd]} {_tl(ps)} δεν είναι εφικτό — η καρφίτσα αγνοείται.")

    # ── H11: teacher weekly workload cap (max_hours = max periods/week) ───────
    teacher_load = {}          # t_id → LinearExpr (periods taught)
    for t in school["teachers"]:
        terms = []
        for k, v in candidates.items():
            if v["teacher"] == t["id"]:
                terms.append(class_map[k[0]]["periods_per_session"] * v["active"])
        load = sum(terms) if terms else 0
        teacher_load[t["id"]] = load
        cap = t.get("max_hours")
        if cap and terms:
            model.add(load <= int(cap))

    # ── Objective ─────────────────────────────────────────────────────────────
    penalties = []

    # S6 — balance teacher load: softly pull down the busiest teacher
    # (minimise the peak weekly load). Small weight so it only breaks ties.
    if settings.get("balance_teacher_load", True):
        loads = [ld for ld in teacher_load.values() if not isinstance(ld, int)]
        if loads:
            peak = model.new_int_var(0, 96, "peak_load")
            for ld in loads:
                model.add(peak >= ld)
            penalties.append(peak)   # weight 1
    for k, v in candidates.items():
        cls = class_map[k[0]]
        loc = room_map[v["room"]]["location"]

        # S1 — wrong location
        pref = cls.get("preferred_location")
        if pref and loc != pref:
            penalties.append(3 * v["active"])

        # S2 — Friday session
        if v["day"] == 4:
            penalties.append(2 * v["active"])

        # S3 — Saturday-preferring classes: penalise weekday sessions instead.
        # Weight 5 so one Saturday session wins even when the only Saturday
        # branch is not the class's preferred location (S1 costs 3).
        if cls.get("saturday_preferred") and v["day"] != 5:
            penalties.append(5 * v["active"])

    # ── S4/S5: schedule stability — prefer previous slots & teachers ──────────
    # Two baselines, same mechanism, different strengths:
    #   previous_slots — last YEAR's schedule (workspace mirroring): pulled
    #     hard towards old day+time (weight 4/session) and the same teacher
    #     at that slot (weight 2/session).
    #   sticky_slots — the PREVIOUS SOLVE (attached automatically on every
    #     re-solve): weight 1 tie-breakers for time, teacher and room, so a
    #     re-solve never reshuffles things without an actual reason.
    def add_stability(cls, slots, w_time, w_teacher, w_room, tag):
        c = cls["id"]
        cls_cands = [(k, v) for k, v in candidates.items() if k[0] == c]
        if not cls_cands:
            return
        for i, slot in enumerate(slots[:cls["sessions_per_week"]]):
            eqs, eqs_teacher, eqs_room = [], [], []
            for k, v in cls_cands:
                if v["day"] != slot["day"]:
                    continue
                eq = model.new_bool_var(f"{tag}_{c}_{i}_{id(v)}")
                model.add(v["start"] == slot["start_tick"]).only_enforce_if(eq)
                model.add_implication(eq, v["active"])
                eqs.append(eq)
                if v["teacher"] == slot.get("teacher_id"):
                    eqs_teacher.append(eq)
                if v["room"] == slot.get("room_id"):
                    eqs_room.append(eq)

            for weight, group, suffix in ((w_time, eqs, "t"),
                                          (w_teacher, eqs_teacher, "te"),
                                          (w_room, eqs_room, "r")):
                if not weight:
                    continue
                matched = model.new_bool_var(f"{tag}_m{suffix}_{c}_{i}")
                if group:
                    model.add_bool_or(group).only_enforce_if(matched)
                else:
                    model.add(matched == 0)
                penalties.append(weight * (1 - matched))

    for cls in school["classes"]:
        if cls["id"] in excluded:
            continue
        if cls.get("previous_slots"):
            add_stability(cls, cls["previous_slots"], 4, 2, 0, "stab")
        if cls.get("sticky_slots"):
            add_stability(cls, cls["sticky_slots"], 1, 1, 1, "stick")

    if penalties:
        model.minimize(sum(penalties))

    # ── solve ─────────────────────────────────────────────────────────────────
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.log_search_progress = False
    status = solver.solve(model)

    result = {
        "status":    solver.status_name(status),
        "objective": solver.objective_value if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "schedule":  [],
        "warnings":  warnings,
    }

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["warnings"].append("Δεν βρέθηκε εφικτό πρόγραμμα — "
                                  "ελέγξτε τους περιορισμούς και τη διαθεσιμότητα.")
        return result

    from .data import tick_label
    for k, v in candidates.items():
        if solver.value(v["active"]) != 1:
            continue
        cls    = class_map[k[0]]
        room   = room_map[v["room"]]
        tchr   = teacher_map[v["teacher"]]
        st     = solver.value(v["start"])
        result["schedule"].append({
            "class_id":    k[0],
            "class_name":  cls["name"],
            "level":       cls["level"],
            "sess_idx":    v["sess_idx"],
            "day_idx":     v["day"],
            "day":         DAYS[v["day"]],
            "start_tick":  st,
            "end_tick":    st + v["duration"],
            "start_label": tick_label(st),
            "end_label":   tick_label(st + v["duration"]),
            "duration_periods": cls["periods_per_session"],
            "room_id":     v["room"],
            "room_name":   room["name"],
            "location":    room["location"],
            "teacher_id":  v["teacher"],
            "teacher":     tchr["name"],
        })

    result["schedule"].sort(key=lambda x: (x["day_idx"], x["start_tick"]))
    return result
