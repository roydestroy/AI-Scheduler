"""
Schedule diff — explain what changed between two solves.

Sessions of the same class are matched in three passes: identical first,
then same-slot (only room/teacher differ), then the remainder pairwise in
week order (those "moved"). Whatever is left is new or removed.

The result feeds the UI: a summary count, plain-language change lines,
and the keys of changed sessions so the weekly grid can highlight them.
"""

from __future__ import annotations

from collections import defaultdict


def _key(e: dict) -> str:
    """Identifies a session block in the NEW schedule for grid highlighting."""
    return f"{e['class_id']}|{e['day_idx']}|{e['start_tick']}"


def _slot(e: dict) -> str:
    return f"{e['day']} {e['start_label']}"


def compute_changes(old_result: dict | None, new_result: dict) -> dict | None:
    """→ {"summary": {...}, "items": [...]} or None when there is no
    previous schedule to compare against."""
    if not old_result or not old_result.get("schedule"):
        return None

    old_by_class: dict[str, list] = defaultdict(list)
    new_by_class: dict[str, list] = defaultdict(list)
    for e in old_result["schedule"]:
        old_by_class[e["class_id"]].append(e)
    for e in new_result.get("schedule", []):
        new_by_class[e["class_id"]].append(e)

    items: list[dict] = []
    unchanged = 0

    for cid in sorted(set(old_by_class) | set(new_by_class)):
        old = sorted(old_by_class.get(cid, []), key=lambda e: (e["day_idx"], e["start_tick"]))
        new = sorted(new_by_class.get(cid, []), key=lambda e: (e["day_idx"], e["start_tick"]))
        name = (new or old)[0]["class_name"]

        # pass 1: identical sessions
        for o in list(old):
            for n in list(new):
                if (o["day_idx"], o["start_tick"], o["room_id"], o["teacher_id"]) == \
                   (n["day_idx"], n["start_tick"], n["room_id"], n["teacher_id"]):
                    old.remove(o); new.remove(n)
                    unchanged += 1
                    break

        # pass 2: same day+time, different room and/or teacher
        for o in list(old):
            for n in list(new):
                if (o["day_idx"], o["start_tick"]) == (n["day_idx"], n["start_tick"]):
                    parts = []
                    if o["teacher_id"] != n["teacher_id"]:
                        parts.append(f"teacher {o['teacher']} → {n['teacher']}")
                    if o["room_id"] != n["room_id"]:
                        parts.append(f"room {o['room_name']} → {n['room_name']}")
                    items.append({
                        "type": "teacher" if o["teacher_id"] != n["teacher_id"] else "room",
                        "class_name": name,
                        "text": f"{name}: {', '.join(parts)} ({_slot(n)})",
                        "keys": [_key(n)],
                    })
                    old.remove(o); new.remove(n)
                    break

        # pass 3: pair the rest in order → moved sessions
        for o, n in zip(list(old), list(new)):
            extra = []
            if o["teacher_id"] != n["teacher_id"]:
                extra.append(f"teacher {o['teacher']} → {n['teacher']}")
            if o["room_id"] != n["room_id"]:
                extra.append(f"room {o['room_name']} → {n['room_name']}")
            items.append({
                "type": "moved",
                "class_name": name,
                "text": f"{name}: {_slot(o)} → {_slot(n)}"
                        + (f" ({', '.join(extra)})" if extra else ""),
                "keys": [_key(n)],
            })
            old.remove(o); new.remove(n)

        # leftovers
        for n in new:
            items.append({
                "type": "added",
                "class_name": name,
                "text": f"{name}: new session {_slot(n)} with {n['teacher']} in {n['room_name']}",
                "keys": [_key(n)],
            })
        for o in old:
            items.append({
                "type": "removed",
                "class_name": name,
                "text": f"{name}: session {_slot(o)} (was {o['teacher']}, {o['room_name']}) "
                        "is no longer scheduled",
                "keys": [],
            })

    order = {"moved": 0, "teacher": 1, "room": 2, "added": 3, "removed": 4}
    items.sort(key=lambda i: (order[i["type"]], i["class_name"]))

    summary = {"unchanged": unchanged}
    for i in items:
        summary[i["type"]] = summary.get(i["type"], 0) + 1

    return {"summary": summary, "items": items}
