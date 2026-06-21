"""
Language School Scheduler — CP-SAT Solver v2
=============================================
Time model
----------
  • 15-min ticks from midnight.  tick(h,m) = h*4 + m//15
  • 1 period = 4 ticks = 60 min  (50 min teaching + 10 min break)
  • A session of N periods occupies ticks [start, start+N*4) in one room.

Decision variables
------------------
  session_start[(c, sess, d, r, t)] — IntVar: start tick on day d
    One variable per (class c, session index sess, day d, room r, teacher t).
    We enumerate only valid (c,sess,d,r,t) combinations.

  active[(c, sess, d, r, t)] — BoolVar: 1 iff this combination is chosen.
    For each (c, sess) exactly one (d, r, t) triple is active.

Hard constraints
----------------
  H1  Each (class, session) has exactly one active assignment.
  H2  Sessions of a class fall on the required day pattern
      (Mon/Wed or Tue/Thu for 2×; triplets for 3×).
  H3  No room overlap: active sessions in the same room on the same day
      must not overlap in ticks.
  H4  No teacher overlap: same rule for teachers.
  H5  Session must fit within the location's operating window on that day.
  H6  Young-learner classes must end by YOUNG_LEARNER_CUTOFF.
  H7  Teacher travel: if teacher ends at location X and next session is
      at location Y on the same day, gap ≥ TRAVEL_PERIODS * TICKS_PER_PERIOD.
  H8  Student blocked windows: session must not overlap any block.
  H9  Sibling groups: at least one shared day across the week.

Soft constraints (objective)
-----------------------------
  S1  Prefer sessions at preferred_location (penalty 3 per violation).
  S2  Penalise Friday sessions (penalty 2 each).
  S3  Penalise young-learner sessions ending after YOUNG_LEARNER_CUTOFF
      (penalty 5 — stronger than location preference).
"""

from __future__ import annotations
from ortools.sat.python import cp_model
from collections import defaultdict
from itertools import combinations

from data import (
    DAYS, ROOMS, TEACHERS, CLASSES, STUDENTS,
    DAY_HOURS, DAY_PAIRS_2X, DAY_TRIPLETS_3X,
    TICKS_PER_PERIOD, YOUNG_LEARNER_CUTOFF, YOUNG_LEARNER_LEVELS,
    TRAVEL_PERIODS, tick, tick_label,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def room_location(room_id: str, room_map: dict) -> str:
    return room_map[room_id]["location"]

def day_window(day: int, location: str) -> dict | None:
    return DAY_HOURS.get((DAYS[day], location))

def valid_start_ticks(day: int, location: str, duration_ticks: int) -> list[int]:
    """All 15-min-aligned start ticks where a session fits inside the window."""
    w = day_window(day, location)
    if w is None:
        return []
    return list(range(w["open"], w["close"] - duration_ticks + 1))

def qualified_teachers(cls: dict) -> list[str]:
    return [t["id"] for t in TEACHERS if cls["level"] in t["qualified_levels"]]

def class_blocked_windows(cls_id: str) -> list[tuple]:
    """Union of all student blocked windows for this class."""
    windows = []
    for s in STUDENTS:
        if s["class_id"] == cls_id:
            windows.extend(s["blocked_windows"])
    return windows

def overlaps(start1: int, dur1: int, start2: int, dur2: int) -> bool:
    return start1 < start2 + dur2 and start2 < start1 + dur1

def sibling_pairs() -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for s in STUDENTS:
        if s["sibling_group"]:
            groups[s["sibling_group"]].append(s["class_id"])
    pairs = []
    for cls_list in groups.values():
        cls_list = list(set(cls_list))
        for a, b in combinations(cls_list, 2):
            pairs.append((a, b))
    return pairs

def day_patterns(sessions_per_week: int) -> list[tuple]:
    if sessions_per_week == 2:
        return DAY_PAIRS_2X
    elif sessions_per_week == 3:
        return DAY_TRIPLETS_3X
    else:
        raise ValueError(f"Unsupported sessions_per_week={sessions_per_week}")


# ── main solver ───────────────────────────────────────────────────────────────

def solve(time_limit_seconds: int = 120) -> dict:
    model  = cp_model.CpModel()
    solver = cp_model.CpSolver()

    class_map   = {c["id"]: c for c in CLASSES}
    room_map    = {r["id"]: r for r in ROOMS}
    teacher_map = {t["id"]: t for t in TEACHERS}

    # ── enumerate candidate (class, sess_idx, day, room, teacher, start_tick) -
    # We build one IntervalVar + BoolVar per candidate.

    # Key:   (c_id, sess_idx, day, room_id, teacher_id)
    # Value: {"active": BoolVar, "start": IntVar, "interval": IntervalVar, "duration": int}
    candidates: dict[tuple, dict] = {}

    for cls in CLASSES:
        c         = cls["id"]
        n_sess    = cls["sessions_per_week"]
        dur       = cls["periods_per_session"] * TICKS_PER_PERIOD
        blocked   = class_blocked_windows(c)
        q_teachers= qualified_teachers(cls)
        patterns  = day_patterns(n_sess)

        # Collect all days that appear in any pattern
        all_days_used = set(d for pat in patterns for d in pat)

        for sess_idx in range(n_sess):
            # Which days could this session index land on?
            days_for_sess = set(pat[sess_idx] for pat in patterns)

            for day in days_for_sess:
                for room in ROOMS:
                    loc = room["location"]
                    starts = valid_start_ticks(day, loc, dur)
                    if not starts:
                        continue
                    for t_id in q_teachers:
                        tchr = teacher_map[t_id]
                        if day not in tchr["available_days"]:
                            continue

                        # Filter starts by student blocked windows
                        valid_starts = []
                        for st in starts:
                            blocked_by_student = False
                            for (bday, bopen, bclose) in blocked:
                                if bday == day and overlaps(st, dur, bopen, bclose - bopen):
                                    blocked_by_student = True
                                    break
                            if not blocked_by_student:
                                valid_starts.append(st)

                        if not valid_starts:
                            continue

                        key = (c, sess_idx, day, room["id"], t_id)
                        active = model.new_bool_var(f"act_{'_'.join(str(x) for x in key)}")
                        start  = model.new_int_var_from_domain(
                            cp_model.Domain.from_values(valid_starts),
                            f"st_{'_'.join(str(x) for x in key)}"
                        )
                        # start is only meaningful when active=1; fix to first valid when inactive
                        # (use optional interval)
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

    # ── H1: each (class, session) has exactly one active assignment ───────────
    for cls in CLASSES:
        c = cls["id"]
        for sess_idx in range(cls["sessions_per_week"]):
            sess_vars = [v["active"] for k, v in candidates.items()
                         if k[0] == c and k[1] == sess_idx]
            if not sess_vars:
                print(f"WARNING: no candidates for {c} session {sess_idx}")
                continue
            model.add(sum(sess_vars) == 1)

    # ── H2: sessions of a class must follow a valid day pattern ──────────────
    for cls in CLASSES:
        c       = cls["id"]
        n_sess  = cls["sessions_per_week"]
        patterns = day_patterns(n_sess)

        # For each pattern, create a bool: "this pattern is chosen"
        pattern_bools = []
        for pat_idx, pattern in enumerate(patterns):
            pat_bool = model.new_bool_var(f"pat_{c}_{pat_idx}")
            pattern_bools.append(pat_bool)
            # If this pattern is chosen, session i must be on pattern[i]
            for sess_idx, required_day in enumerate(pattern):
                # All active candidates for this (class, sess_idx) on required_day
                on_day = [v["active"] for k, v in candidates.items()
                          if k[0] == c and k[1] == sess_idx and k[2] == required_day]
                if on_day:
                    model.add(sum(on_day) == 1).only_enforce_if(pat_bool)
                else:
                    # No valid candidate on this day → pattern impossible
                    model.add(pat_bool == 0)
                # Sessions NOT on required_day must be 0 if this pattern chosen
                off_day = [v["active"] for k, v in candidates.items()
                           if k[0] == c and k[1] == sess_idx and k[2] != required_day]
                for ov in off_day:
                    model.add(ov == 0).only_enforce_if(pat_bool)

        # Exactly one pattern chosen
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

    # ── H5: session fits in window — already enforced by valid_start_ticks ────
    # H6: young-learner end time ───────────────────────────────────────────────
    # Applied as hard constraint (children really shouldn't go late)
    for k, v in candidates.items():
        c = k[0]
        cls = class_map[c]
        if cls["level"] in YOUNG_LEARNER_LEVELS:
            # start + duration <= cutoff  (only enforce if active)
            model.add(v["start"] + v["duration"] <= YOUNG_LEARNER_CUTOFF).only_enforce_if(v["active"])

    # ── H7: teacher travel time between locations on same day ─────────────────
    travel_ticks = TRAVEL_PERIODS * TICKS_PER_PERIOD
    # Group active candidates by (teacher, day)
    by_td: dict[tuple, list] = defaultdict(list)
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
                # Both active → gap between them must be ≥ travel_ticks
                both_active = model.new_bool_var(
                    f"both_{t_id}_{day}_{id(a)}_{id(b)}")
                model.add_bool_and([a["active"], b["active"]]).only_enforce_if(both_active)
                model.add_bool_or([a["active"].negated(), b["active"].negated()]).only_enforce_if(both_active.negated())

                # a ends before b starts → gap >= travel
                a_before_b = model.new_bool_var(f"ab_{id(a)}_{id(b)}")
                model.add(b["start"] >= a["start"] + a["duration"] + travel_ticks).only_enforce_if([both_active, a_before_b])
                model.add(a["start"] >= b["start"] + b["duration"] + travel_ticks).only_enforce_if([both_active, a_before_b.negated()])

    # ── H8: student blocks — already filtered in candidate generation ─────────
    # H9: sibling groups ───────────────────────────────────────────────────────
    for (c1, c2) in sibling_pairs():
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

    # ── Objective ─────────────────────────────────────────────────────────────
    penalties = []

    for k, v in candidates.items():
        c   = k[0]
        cls = class_map[c]
        day = v["day"]
        loc = room_map[v["room"]]["location"]

        # S1 — wrong location
        pref = cls.get("preferred_location")
        if pref and loc != pref:
            penalties.append(3 * v["active"])

        # S2 — Friday session
        if day == 4:
            penalties.append(2 * v["active"])

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
        "warnings":  [],
    }

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["warnings"].append("No feasible schedule found — check constraints / availability.")
        return result

    for k, v in candidates.items():
        if solver.value(v["active"]) != 1:
            continue
        c      = k[0]
        cls    = class_map[c]
        room   = room_map[v["room"]]
        tchr   = teacher_map[v["teacher"]]
        st     = solver.value(v["start"])
        result["schedule"].append({
            "class_id":    c,
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
