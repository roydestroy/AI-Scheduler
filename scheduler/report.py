"""
Report v2 — pretty-print the v2 schedule.
"""
from solver import solve
from data import DAYS, ROOMS, TEACHERS, CLASSES, STUDENTS, tick_label, YOUNG_LEARNER_LEVELS
from collections import defaultdict

def run():
    print("=" * 72)
    print("  LANGUAGE SCHOOL SCHEDULER v2 — solving …")
    print("=" * 72)

    result = solve(time_limit_seconds=120)
    print(f"\n  Solver status : {result['status']}")
    if result["objective"] is not None:
        print(f"  Penalty score : {result['objective']}  (lower = better)")
    for w in result["warnings"]:
        print(f"  ⚠️  {w}")

    if not result["schedule"]:
        return

    sched = result["schedule"]
    print(f"  Total sessions: {len(sched)}\n")

    # ── 1. Daily view ─────────────────────────────────────────────────────────
    print("─" * 72)
    print("  DAILY VIEW")
    print("─" * 72)
    by_day = defaultdict(list)
    for e in sched:
        by_day[e["day_idx"]].append(e)

    for day_idx in range(6):
        day_entries = sorted(by_day.get(day_idx, []), key=lambda x: x["start_tick"])
        if not day_entries:
            continue
        print(f"\n  {DAYS[day_idx]}")
        print(f"  {'Time':13} {'Class':28} {'Room':10} {'Loc':4} {'Teacher':12} {'Dur':5}")
        print("  " + "─" * 68)
        for e in day_entries:
            time_range = f"{e['start_label']}–{e['end_label']}"
            dur_str    = f"{e['duration_periods']}p"
            print(f"  {time_range:13} {e['class_name']:28} {e['room_name']:10} "
                  f"{e['location']:4} {e['teacher']:12} {dur_str}")

    # ── 2. Per-teacher view ───────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  TEACHER SCHEDULES")
    print("─" * 72)
    by_teacher = defaultdict(list)
    for e in sched:
        by_teacher[e["teacher"]].append(e)

    for teacher in sorted(by_teacher):
        sessions = sorted(by_teacher[teacher], key=lambda x: (x["day_idx"], x["start_tick"]))
        total_periods = sum(e["duration_periods"] for e in sessions)
        print(f"\n  {teacher}  —  {len(sessions)} sessions / {total_periods * 50} min teaching/week")
        prev_loc, prev_day = None, None
        for e in sessions:
            travel = ""
            if prev_day == e["day_idx"] and prev_loc and prev_loc != e["location"]:
                travel = f"  ✈ from {prev_loc}"
            print(f"    {e['day']:3} {e['start_label']}–{e['end_label']}  "
                  f"{e['class_name']:28} @{e['location']} {e['room_name']}{travel}")
            prev_loc, prev_day = e["location"], e["day_idx"]

    # ── 3. Sibling verification ───────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  SIBLING GROUP VERIFICATION")
    print("─" * 72)
    sib_groups = defaultdict(list)
    for s in STUDENTS:
        if s["sibling_group"]:
            sib_groups[s["sibling_group"]].append(s)

    class_days = defaultdict(set)
    for e in sched:
        class_days[e["class_id"]].add(e["day"])

    for grp, members in sib_groups.items():
        print(f"\n  Group {grp}:")
        days_per_member = {}
        for m in members:
            d = class_days[m["class_id"]]
            days_per_member[m["name"]] = d
            print(f"    {m['name']:14} ({m['class_id']})  days: {sorted(d)}")
        vals = list(days_per_member.values())
        shared = vals[0]
        for v in vals[1:]:
            shared = shared & v
        icon = "✅" if shared else "❌"
        print(f"    {icon} Shared: {sorted(shared) if shared else 'NONE'}")

    # ── 4. Student constraint verification ───────────────────────────────────
    print("\n" + "─" * 72)
    print("  STUDENT CONSTRAINT VERIFICATION")
    print("─" * 72)
    for s in STUDENTS:
        if not s["blocked_windows"]:
            continue
        violations = []
        for e in sched:
            if e["class_id"] != s["class_id"]:
                continue
            for (bday, bopen, bclose) in s["blocked_windows"]:
                if e["day_idx"] == bday:
                    # check overlap
                    if e["start_tick"] < bclose and e["end_tick"] > bopen:
                        violations.append(f"{e['day']} {e['start_label']}")
        icon = "✅" if not violations else f"❌ VIOLATION {violations}"
        print(f"  {s['name']:14} {icon}  ({s.get('note','')})")

    # ── 5. Day-pattern summary ────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  CLASS DAY PATTERNS")
    print("─" * 72)
    print(f"  {'Class':28} {'Pattern':20} {'Periods/sess':13} {'Total h/wk'}")
    print("  " + "─" * 65)
    for cls in CLASSES:
        c = cls["id"]
        days = sorted(class_days[c])
        pattern = " / ".join(days)
        total_min = cls["periods_per_session"] * cls["sessions_per_week"] * 50
        print(f"  {cls['name']:28} {pattern:20} {cls['periods_per_session']:13} "
              f"{total_min//60}h{total_min%60:02d}m")

    print("\n" + "=" * 72)
    print("  Done.")
    print("=" * 72)

if __name__ == "__main__":
    run()
