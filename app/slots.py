"""
Free-slot finder — where is a teacher or room free this week?

Given the last solved schedule + the school config, returns the open
15-min-aligned windows for a teacher or room, respecting opening hours,
existing sessions, and (for teachers) their available days and blocked
windows. Used by the UI to answer "πότε χωράει ένα ιδιαίτερο;".
"""

from __future__ import annotations

from scheduler.data import DAYS, tick_label


def _busy_for(schedule: list, key: str, value: str) -> dict[int, list]:
    """day_idx → [(start, end)] busy intervals for teacher_id/room_id."""
    busy: dict[int, list] = {d: [] for d in range(len(DAYS))}
    for e in schedule:
        if e.get(key) == value:
            busy[e["day_idx"]].append((e["start_tick"], e["end_tick"]))
    return busy


def _free_windows(open_t: int, close_t: int, busy: list, blocks: list,
                  min_len: int) -> list[tuple[int, int]]:
    """Subtract busy+blocks from [open,close], keep gaps >= min_len."""
    taken = sorted(busy + blocks)
    free = []
    cursor = open_t
    for bs, be in taken:
        if bs > cursor:
            free.append((cursor, min(bs, close_t)))
        cursor = max(cursor, be)
        if cursor >= close_t:
            break
    if cursor < close_t:
        free.append((cursor, close_t))
    return [(s, e) for s, e in free if e - s >= min_len]


def find_slots(school: dict, schedule: list, kind: str, entity_id: str,
               periods: int = 2) -> dict:
    """kind: 'teacher' | 'room'. Returns {label, days:[{day, windows:[...]}]}."""
    tpp = school["settings"]["ticks_per_period"]
    need = periods * tpp

    if kind == "teacher":
        t = next((x for x in school["teachers"] if x["id"] == entity_id), None)
        if not t:
            return {"error": f"Άγνωστος καθηγητής '{entity_id}'."}
        label = t["name"]
        avail_days = set(t["available_days"])
        blocks_by_day: dict[int, list] = {d: [] for d in range(len(DAYS))}
        for (bd, bo, bc) in t.get("blocked_windows", []):
            blocks_by_day[bd].append((bo, bc))
        busy = _busy_for(schedule, "teacher_id", entity_id)
        # a teacher is only relevant at locations that are open; scan every loc
        locations = list(school["locations"])
    elif kind == "room":
        r = next((x for x in school["rooms"] if x["id"] == entity_id), None)
        if not r:
            return {"error": f"Άγνωστη αίθουσα '{entity_id}'."}
        label = f"{r['name']} ({r['location']})"
        avail_days = set(range(len(DAYS)))
        blocks_by_day = {d: [] for d in range(len(DAYS))}
        busy = _busy_for(schedule, "room_id", entity_id)
        locations = [r["location"]]
    else:
        return {"error": "kind must be teacher or room"}

    out_days = []
    for d in range(len(DAYS)):
        if d not in avail_days:
            continue
        # union of opening windows across the relevant locations that day
        day_windows = []
        for loc in locations:
            w = school["day_hours"].get(DAYS[d], {}).get(loc)
            if w:
                day_windows.append((w["open"], w["close"], loc))
        if not day_windows:
            continue
        windows = []
        for (open_t, close_t, loc) in day_windows:
            for (fs, fe) in _free_windows(open_t, close_t, busy[d],
                                          blocks_by_day[d], need):
                windows.append({
                    "start": fs, "end": fe, "location": loc,
                    "label": f"{tick_label(fs)}–{tick_label(fe)}"
                             + (f" @{loc}" if kind == "teacher" and len(locations) > 1 else ""),
                })
        if windows:
            out_days.append({"day_idx": d, "day": DAYS[d], "windows": windows})

    return {"label": label, "kind": kind, "periods": periods, "days": out_days}
