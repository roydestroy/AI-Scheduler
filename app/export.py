"""
Schedule exports: printable PDF and iCalendar (.ics).

The .ics file contains one weekly-recurring event per session, so it can
be imported straight into Google Calendar, Outlook or Apple Calendar
(Google Calendar: Settings → Import & export → Import).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from fpdf import FPDF

from scheduler.data import DAYS


# ── iCalendar ─────────────────────────────────────────────────────────────────

def _next_date_for(day_idx: int, start: date | None = None) -> date:
    today = start or date.today()
    return today + timedelta(days=(day_idx - today.weekday()) % 7)


def _tick_to_hm(t: int) -> tuple[int, int]:
    return t // 4, (t % 4) * 15


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(result: dict, school: dict) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Language School Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:School timetable",
    ]
    for i, e in enumerate(result.get("schedule", [])):
        d = _next_date_for(e["day_idx"])
        h1, m1 = _tick_to_hm(e["start_tick"])
        h2, m2 = _tick_to_hm(e["end_tick"])
        loc_name = school["locations"].get(e["location"], e["location"])
        lines += [
            "BEGIN:VEVENT",
            f"UID:session-{i}-{e['class_id']}-{e['day_idx']}@school-scheduler",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{d.strftime('%Y%m%d')}T{h1:02d}{m1:02d}00",
            f"DTEND:{d.strftime('%Y%m%d')}T{h2:02d}{m2:02d}00",
            "RRULE:FREQ=WEEKLY",
            f"SUMMARY:{_ics_escape(e['class_name'])} ({_ics_escape(e['teacher'])})",
            f"LOCATION:{_ics_escape(loc_name + ' — ' + e['room_name'])}",
            f"DESCRIPTION:{_ics_escape('Level ' + e['level'] + ', teacher ' + e['teacher'] + ', room ' + e['room_name'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ── PDF ───────────────────────────────────────────────────────────────────────

GREEK_DAYS_FULL = {"Mon": "Δευτέρα", "Tue": "Τρίτη", "Wed": "Τετάρτη",
                   "Thu": "Πέμπτη", "Fri": "Παρασκευή", "Sat": "Σάββατο"}

#: candidate Unicode fonts (needed for Greek text; fpdf core fonts are latin-1)
_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


def _setup_font(pdf: FPDF) -> tuple[str, bool]:
    """Register a Unicode font if one exists. → (family, unicode_ok)."""
    from pathlib import Path
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).exists():
            pdf.add_font("app", "", regular)
            pdf.add_font("app", "B", bold if Path(bold).exists() else regular)
            return "app", True
    return "Helvetica", False


def build_pdf(result: dict, school: dict) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    family, unicode_ok = _setup_font(pdf)

    def _txt(s: str) -> str:
        return str(s) if unicode_ok else str(s).encode("latin-1", "replace").decode("latin-1")
    _latin = _txt  # existing call sites

    sched = result.get("schedule", [])

    # ── page 1+: weekly overview, one block per day ──────────────────────────
    pdf.add_page()
    pdf.set_font(family, "B", 16)
    pdf.cell(0, 10, _txt("Εβδομαδιαίο πρόγραμμα"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 9)
    pdf.set_text_color(110)
    pdf.cell(0, 6,
             _txt(f"Κατάσταση {result.get('status')} - ποινή {result.get('objective')} - "
                  f"{len(sched)} μαθήματα - δημιουργήθηκε {date.today().isoformat()}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(2)

    widths = (26, 78, 40, 52, 46)
    headers = tuple(_txt(h) for h in ("Ώρα", "Τμήμα", "Αίθουσα", "Κτήριο", "Καθηγητής"))

    by_day = defaultdict(list)
    for e in sched:
        by_day[e["day_idx"]].append(e)

    for day_idx in range(len(DAYS)):
        entries = sorted(by_day.get(day_idx, []), key=lambda x: (x["start_tick"], x["room_name"]))
        if not entries:
            continue
        if pdf.get_y() > 150:
            pdf.add_page()
        pdf.set_font(family, "B", 12)
        pdf.cell(0, 8, _txt(GREEK_DAYS_FULL.get(DAYS[day_idx], DAYS[day_idx])), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(family, "B", 9)
        pdf.set_fill_color(235)
        for w, h in zip(widths, headers):
            pdf.cell(w, 6, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font(family, "", 9)
        for e in entries:
            loc_name = school["locations"].get(e["location"], e["location"])
            cells = (f"{e['start_label']}-{e['end_label']}", e["class_name"],
                     e["room_name"], loc_name, e["teacher"])
            for w, c in zip(widths, cells):
                pdf.cell(w, 6, _latin(str(c)), border=1)
            pdf.ln()
        pdf.ln(3)

    # ── per-teacher pages ────────────────────────────────────────────────────
    by_teacher = defaultdict(list)
    for e in sched:
        by_teacher[e["teacher"]].append(e)

    if by_teacher:
        pdf.add_page()
        pdf.set_font(family, "B", 16)
        pdf.cell(0, 10, _txt("Προγράμματα καθηγητών"), new_x="LMARGIN", new_y="NEXT")
        for teacher in sorted(by_teacher):
            entries = sorted(by_teacher[teacher], key=lambda x: (x["day_idx"], x["start_tick"]))
            total_min = sum(e["duration_periods"] for e in entries) * 50
            if pdf.get_y() > 165:
                pdf.add_page()
            pdf.set_font(family, "B", 12)
            pdf.cell(0, 8, _latin(f"{teacher}  -  {len(entries)} μαθήματα, "
                                  f"{total_min // 60}ώ{total_min % 60:02d} διδασκαλία/εβδομάδα"),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(family, "", 9)
            for e in entries:
                loc_name = school["locations"].get(e["location"], e["location"])
                pdf.cell(0, 6, _latin(
                    f"{GREEK_DAYS_FULL.get(e['day'], e['day'])[:3]}  {e['start_label']}-{e['end_label']}   "
                    f"{e['class_name']}   @ {loc_name}, {e['room_name']}"),
                    new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

    return bytes(pdf.output())


# ── parent notice slips ───────────────────────────────────────────────────────

def build_slips(result: dict, school: dict, by: str = "class") -> bytes:
    """Printable notices for parents: the weekly sessions of each class
    (by='class') or a personal slip per student (by='student')."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    family, unicode_ok = _setup_font(pdf)

    def _txt(s):
        return str(s) if unicode_ok else str(s).encode("latin-1", "replace").decode("latin-1")

    sched = result.get("schedule", [])
    by_class = defaultdict(list)
    for e in sched:
        by_class[e["class_id"]].append(e)
    for cid in by_class:
        by_class[cid].sort(key=lambda x: (x["day_idx"], x["start_tick"]))

    def class_lines(cid):
        lines = []
        for e in by_class.get(cid, []):
            loc = school["locations"].get(e["location"], e["location"])
            lines.append(f"{GREEK_DAYS_FULL.get(e['day'], e['day'])}  "
                         f"{e['start_label']}-{e['end_label']}  ·  {loc}, "
                         f"{e['room_name']}  ·  {e['teacher']}")
        return lines

    def slip(title, subtitle, lines):
        pdf.add_page()
        pdf.set_font(family, "B", 18)
        pdf.cell(0, 12, _txt("Ωρολόγιο Πρόγραμμα"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(family, "B", 14)
        pdf.cell(0, 9, _txt(title), new_x="LMARGIN", new_y="NEXT")
        if subtitle:
            pdf.set_font(family, "", 11); pdf.set_text_color(110)
            pdf.cell(0, 7, _txt(subtitle), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0)
        pdf.ln(3)
        pdf.set_font(family, "", 12)
        if lines:
            for ln in lines:
                pdf.cell(6, 8, _txt("•"))
                pdf.cell(0, 8, _txt(ln), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(0, 8, _txt("(δεν έχει προγραμματιστεί μάθημα)"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)
        pdf.set_font(family, "", 9); pdf.set_text_color(130)
        pdf.cell(0, 6, _txt("Οι ώρες μπορεί να αλλάξουν — παρακαλούμε επιβεβαιώστε με τη γραμματεία."),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0)

    class_map = {c["id"]: c for c in school["classes"]}
    if by == "student":
        for s in sorted(school["students"], key=lambda x: x.get("name", "")):
            cls = class_map.get(s["class_id"])
            if not cls:
                continue
            slip(s["name"], f"Τμήμα {cls['name']} — επίπεδο {cls['level']}",
                 class_lines(s["class_id"]))
    else:
        for c in school["classes"]:
            if c["id"] in by_class:
                slip(c["name"], f"Επίπεδο {c['level']}", class_lines(c["id"]))

    if pdf.page_no() == 0:
        pdf.add_page()
        pdf.set_font(family, "", 12)
        pdf.cell(0, 10, _txt("Δεν υπάρχει πρόγραμμα για εκτύπωση."))
    return bytes(pdf.output())
