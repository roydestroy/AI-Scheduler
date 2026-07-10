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

def _latin(s: str) -> str:
    """fpdf core fonts are latin-1 only."""
    return s.encode("latin-1", "replace").decode("latin-1")


def build_pdf(result: dict, school: dict) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)

    sched = result.get("schedule", [])

    # ── page 1+: weekly overview, one block per day ──────────────────────────
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Weekly timetable", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110)
    pdf.cell(0, 6,
             f"Status {result.get('status')} - penalty {result.get('objective')} - "
             f"{len(sched)} sessions - generated {date.today().isoformat()}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(2)

    widths = (26, 78, 40, 52, 46)
    headers = ("Time", "Class", "Room", "Location", "Teacher")

    by_day = defaultdict(list)
    for e in sched:
        by_day[e["day_idx"]].append(e)

    for day_idx in range(len(DAYS)):
        entries = sorted(by_day.get(day_idx, []), key=lambda x: (x["start_tick"], x["room_name"]))
        if not entries:
            continue
        if pdf.get_y() > 150:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, DAYS[day_idx], new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(235)
        for w, h in zip(widths, headers):
            pdf.cell(w, 6, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
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
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "Teacher schedules", new_x="LMARGIN", new_y="NEXT")
        for teacher in sorted(by_teacher):
            entries = sorted(by_teacher[teacher], key=lambda x: (x["day_idx"], x["start_tick"]))
            total_min = sum(e["duration_periods"] for e in entries) * 50
            if pdf.get_y() > 165:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, _latin(f"{teacher}  -  {len(entries)} sessions, "
                                  f"{total_min // 60}h{total_min % 60:02d} teaching/week"),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            for e in entries:
                loc_name = school["locations"].get(e["location"], e["location"])
                pdf.cell(0, 6, _latin(
                    f"{e['day']}  {e['start_label']}-{e['end_label']}   "
                    f"{e['class_name']}   @ {loc_name}, {e['room_name']}"),
                    new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

    return bytes(pdf.output())
