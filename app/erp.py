"""
ERP import — pull students & enrolments from the school's SQL Server ERPs.

Each branch has its own ERP database; each is configured as an import
*source* bound to one location. Importing a source updates ONLY that
location's ERP-managed records (students/classes it created earlier),
never manual entries or the other branch.

Configuration lives in data/erp_sources.json (see erp_sources.example.json
in the repo root). Two source types:

  sqlserver — reads the ERP directly via pyodbc (Windows auth locally,
              SQL auth across the network / Tailscale).
  jsonfile  — reads the same row shape from a JSON file; used for tests
              and as a fallback when the DB is not reachable remotely.

Level codes (Enrollments.LevelOrClass, e.g. "EJ3") become the app's
levels directly. A persistent mapping table (data/erp_mapping.json)
controls, per code: whether to import it, periods per session, sessions
per week, and the young-learner flag. Unknown codes get sensible
defaults and are highlighted in the preview for review.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .store import _DATA_DIR  # same data directory as school.json

SOURCES_FILE = _DATA_DIR / "erp_sources.json"
MAPPING_FILE = _DATA_DIR / "erp_mapping.json"

#: the queries run against each ERP database (read-only).
#: The academic period is an explicit choice in the UI — the ERP's
#: IsCurrent flag is only used as the preselected default, never silently.
ENROLLMENT_QUERY = """
SELECT s.StudentId, s.FirstName, s.LastName, s.SiblingGroupId, e.LevelOrClass
FROM Enrollments e
JOIN Students s        ON s.StudentId = e.StudentId
JOIN AcademicPeriods p ON p.AcademicPeriodId = e.AcademicPeriodId
WHERE {period_filter}
  AND e.Status = 'Active'
  AND e.IsStopped = 0
  AND s.Discontinued = 0
"""

PERIODS_QUERY = """
SELECT AcademicPeriodId, Name, IsCurrent
FROM AcademicPeriods
ORDER BY Name DESC
"""


class ErpError(Exception):
    pass


# ── configuration ─────────────────────────────────────────────────────────────

def load_sources() -> list[dict]:
    if not SOURCES_FILE.exists():
        return []
    try:
        cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ErpError(f"{SOURCES_FILE} is not valid JSON: {e}")
    sources = cfg.get("sources", [])
    for s in sources:
        for field in ("key", "name", "location"):
            if not s.get(field):
                raise ErpError(f"Every ERP source needs '{field}' (see erp_sources.example.json).")
    return sources


def get_source(key: str) -> dict:
    for s in load_sources():
        if s["key"] == key:
            return s
    raise ErpError(f"Unknown ERP source '{key}'. Configured: "
                   f"{', '.join(s['key'] for s in load_sources()) or 'none'}.")


def public_sources() -> list[dict]:
    """Source list safe to show in the UI (no connection strings)."""
    return [{"key": s["key"], "name": s["name"], "location": s["location"],
             "type": s.get("type", "sqlserver")} for s in load_sources()]


# ── level-code mapping ────────────────────────────────────────────────────────

def _greek(code: str) -> bool:
    return bool(re.search(r"[Α-Ωα-ωίϊΐόάέύϋΰήώ]", code))


def default_mapping_entry(code: str) -> dict:
    return {
        # Greek-named codes are the school-study groups (ΔΗΜΟΤΙΚΟΥ κτλ.) —
        # skipped by default; tick them in the mapping table to schedule them.
        "import": not _greek(code),
        "periods_per_session": 2,
        "sessions_per_week": 2,
        "young_learner": _greek(code),
    }


def load_mapping() -> dict:
    if MAPPING_FILE.exists():
        return json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    return {}


def save_mapping(mapping: dict) -> None:
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_FILE.write_text(json.dumps(mapping, indent=2, ensure_ascii=False),
                            encoding="utf-8")


def ensure_mapping(mapping: dict, codes: list[str]) -> dict:
    """Fill in defaults for codes not yet in the mapping."""
    out = dict(mapping)
    for code in codes:
        if code not in out:
            out[code] = default_mapping_entry(code)
    return out


# ── fetching rows ─────────────────────────────────────────────────────────────

def fetch_periods(source: dict) -> list[dict]:
    """→ [{id, name, is_current}] — the academic periods available in the ERP.
    File sources have no notion of periods and return []."""
    if source.get("type", "sqlserver") == "jsonfile":
        return []
    rows = _query_sqlserver(source, PERIODS_QUERY)
    return [{
        "id": str(r["AcademicPeriodId"]),
        "name": r["Name"],
        "is_current": bool(r["IsCurrent"]),
    } for r in rows]


def fetch_rows(source: dict, academic_period_id: str | None = None) -> list[dict]:
    """→ [{student_id, first_name, last_name, sibling_group_id, level_code}]
    academic_period_id=None falls back to the ERP's IsCurrent period."""
    stype = source.get("type", "sqlserver")
    if stype == "jsonfile":
        path = Path(source["path"])
        if not path.exists():
            raise ErpError(f"ERP file source not found: {path}")
        rows = json.loads(path.read_text(encoding="utf-8"))
    elif stype == "sqlserver":
        rows = _fetch_sqlserver(source, academic_period_id)
    else:
        raise ErpError(f"Unknown ERP source type '{stype}'.")

    cleaned = []
    for r in rows:
        code = (r.get("level_code") or "").strip()
        if not code:
            continue
        cleaned.append({
            "student_id":       str(r["student_id"]),
            "first_name":       (r.get("first_name") or "").strip(),
            "last_name":        (r.get("last_name") or "").strip(),
            "sibling_group_id": str(r["sibling_group_id"]) if r.get("sibling_group_id") else None,
            "level_code":       code,
        })
    return cleaned


def _query_sqlserver(source: dict, query: str, params: tuple = ()) -> list[dict]:
    try:
        import pyodbc
    except ImportError:
        raise ErpError("pyodbc is not installed — run: pip install pyodbc")

    conn_str = source.get("conn_str")
    if not conn_str:
        raise ErpError(f"Source '{source['key']}' has no conn_str.")

    try:
        with pyodbc.connect(conn_str, timeout=10) as conn:
            cur = conn.cursor()
            cur.execute(query, *params) if params else cur.execute(query)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except pyodbc.Error as e:
        raise ErpError(
            f"Αδύνατη η ανάγνωση της βάσης ERP για '{source['name']}': {e}. "
            "Ελέγξτε το connection string, ότι ο SQL Server δέχεται συνδέσεις TCP/IP "
            "και ότι το μηχάνημα είναι προσβάσιμο (Tailscale ενεργό;)."
        )


def _fetch_sqlserver(source: dict, academic_period_id: str | None = None) -> list[dict]:
    if academic_period_id:
        query = ENROLLMENT_QUERY.format(period_filter="e.AcademicPeriodId = ?")
        raw = _query_sqlserver(source, query, (academic_period_id,))
    else:
        query = ENROLLMENT_QUERY.format(period_filter="p.IsCurrent = 1")
        raw = _query_sqlserver(source, query)

    return [{
        "student_id":       r["StudentId"],
        "first_name":       r["FirstName"],
        "last_name":        r["LastName"],
        "sibling_group_id": r["SiblingGroupId"],
        "level_code":       r["LevelOrClass"],
    } for r in raw]


# ── plan / apply ──────────────────────────────────────────────────────────────

def _slug(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9Α-Ωα-ω]+", "_", code).strip("_")


def _short(guid: str) -> str:
    """Stable 12-hex-char id derived from the full GUID. Hashing (rather
    than truncating) keeps ids distinct even for sequential ERP GUIDs."""
    return hashlib.md5(guid.strip().lower().encode()).hexdigest()[:12]


def build_plan(school: dict, source: dict, rows: list[dict], mapping: dict) -> dict:
    """Compute what an import would change. Returns the plan + the merged
    school; nothing is saved here."""
    key, loc = source["key"], source["location"]
    if loc not in school["locations"]:
        raise ErpError(f"Source '{key}' maps to unknown location '{loc}'. "
                       f"Valid locations: {', '.join(school['locations'])}.")

    codes = sorted({r["level_code"] for r in rows})
    mapping = ensure_mapping(mapping, codes)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["level_code"]] = counts.get(r["level_code"], 0) + 1

    new_school = copy.deepcopy(school)
    plan = {
        "source": key,
        "location": loc,
        "total_rows": len(rows),
        "codes": [{"code": c, "students": counts[c], **mapping[c]} for c in codes],
        "new_classes": [], "removed_classes": [],
        "new_students": [], "updated_students": [], "removed_students": [],
        "skipped": {c: counts[c] for c in codes if not mapping[c]["import"]},
        "baseline_carried": [],
        "warnings": [],
    }

    imported_codes = [c for c in codes if mapping[c]["import"]]

    # ── classes: one per imported code at this location ───────────────────────
    class_by_code: dict[str, str] = {}
    existing_classes = {c["id"]: c for c in new_school["classes"]}
    for code in imported_codes:
        cid = f"{key}-{_slug(code)}"
        class_by_code[code] = cid
        m = mapping[code]
        if cid in existing_classes:
            c = existing_classes[cid]
            c.update({"level": code,
                      "periods_per_session": int(m["periods_per_session"]),
                      "sessions_per_week": int(m["sessions_per_week"])})
        else:
            new_school["classes"].append({
                "id": cid,
                "name": f"{code} ({source['name']})",
                "level": code,
                "periods_per_session": int(m["periods_per_session"]),
                "sessions_per_week": int(m["sessions_per_week"]),
                "preferred_location": loc,
                "erp_source": key,
            })
            plan["new_classes"].append(f"{code} ({source['name']}) — {counts[code]} μαθητές")

    # drop previously-imported classes of this source whose code vanished
    keep_ids = set(class_by_code.values())
    for c in list(new_school["classes"]):
        if c.get("erp_source") == key and c["id"] not in keep_ids:
            plan["removed_classes"].append(c["name"])
            new_school["classes"].remove(c)

    # ── levels & young-learner set follow the mapping ─────────────────────────
    settings = new_school["settings"]
    for code in imported_codes:
        if code not in settings["levels"]:
            settings["levels"].append(code)
        young = set(settings["young_learner_levels"])
        if mapping[code]["young_learner"]:
            young.add(code)
        else:
            young.discard(code)
        settings["young_learner_levels"] = sorted(young)

    # ── students ──────────────────────────────────────────────────────────────
    existing = {s["id"]: s for s in new_school["students"]}
    seen_ids: set[str] = set()
    per_student_count: dict[str, int] = {}
    seen_pairs: set[tuple] = set()

    for r in sorted(rows, key=lambda x: (x["student_id"], x["level_code"])):
        code = r["level_code"]
        if code not in class_by_code:
            continue
        pair = (r["student_id"], code)
        if pair in seen_pairs:      # duplicate enrolment rows in the ERP
            continue
        seen_pairs.add(pair)
        base_id = f"{key}-{_short(r['student_id'])}"
        n = per_student_count.get(base_id, 0) + 1
        per_student_count[base_id] = n
        sid = base_id if n == 1 else f"{base_id}-{n}"
        if n == 2:
            plan["warnings"].append(
                f"Ο/Η {r['first_name']} {r['last_name']} έχει πολλαπλές εγγραφές προς "
                "προγραμματισμό — προσοχή: ο επιλύτης δεν αποτρέπει ακόμη τη χρονική "
                "επικάλυψή τους.")

        name = f"{r['first_name']} {r['last_name']}".strip()
        sib = f"{key}-{_short(r['sibling_group_id'])}" if r["sibling_group_id"] else None
        seen_ids.add(sid)

        if sid in existing:
            s = existing[sid]
            changes = (s["name"] != name or s["class_id"] != class_by_code[code]
                       or s["sibling_group"] != sib)
            if changes:
                if s["class_id"] != class_by_code[code]:
                    plan["updated_students"].append(f"{name}: → {code}")
                s.update({"name": name, "class_id": class_by_code[code], "sibling_group": sib})
            # blocked_windows and note are manual data — always preserved
        else:
            new_school["students"].append({
                "id": sid, "name": name, "class_id": class_by_code[code],
                "sibling_group": sib, "blocked_windows": [], "note": "",
                "erp_source": key,
            })
            plan["new_students"].append(f"{name} → {code}")

    # remove ERP-managed students of this source that disappeared from the ERP
    for s in list(new_school["students"]):
        if s.get("erp_source") == key and s["id"] not in seen_ids:
            plan["removed_students"].append(s["name"])
            new_school["students"].remove(s)

    # ── carry schedule baselines with the COHORT, not the class id ────────────
    # When a workspace was seeded from last year, classes hold `previous_slots`
    # (their old day/time/teacher). After importing the new period, the kids
    # of last year's EJ2 are now in class fil-EJ3 — so fil-EJ3 must inherit
    # EJ2's old slot, not keep the slot of last year's (different) EJ3 group.
    # We match each imported class to the old class most of its students came
    # from, and move the baseline accordingly.
    def _base(sid: str) -> str:
        return re.sub(r"-\d+$", "", sid)

    old_members: dict[str, set] = defaultdict(set)
    for s in school["students"]:
        if s.get("erp_source") == key:
            old_members[s["class_id"]].add(_base(s["id"]))
    old_classes = {c["id"]: c for c in school["classes"]}
    old_slots = {cid: c.get("previous_slots") for cid, c in old_classes.items()}

    if any(old_slots.values()):
        new_members: dict[str, set] = defaultdict(set)
        for s in new_school["students"]:
            if s.get("erp_source") == key:
                new_members[s["class_id"]].add(_base(s["id"]))

        for c in new_school["classes"]:
            if c.get("erp_source") != key:
                continue
            members = new_members.get(c["id"], set())
            if not members:
                continue
            donor, overlap = None, 0
            for old_id, old_set in old_members.items():
                n = len(members & old_set)
                if n > overlap:
                    donor, overlap = old_id, n
            if donor and overlap * 2 >= len(members):
                slots = old_slots.get(donor)
                if slots:
                    if donor != c["id"]:
                        plan["baseline_carried"].append(
                            f"Το {c['name']} κρατά την παλιά ώρα του "
                            f"{old_classes[donor]['name']} (ίδιοι μαθητές, ένα επίπεδο πάνω)")
                    c["previous_slots"] = copy.deepcopy(slots)
                else:
                    c.pop("previous_slots", None)

    plan["mapping"] = mapping
    return {"plan": plan, "school": new_school}
