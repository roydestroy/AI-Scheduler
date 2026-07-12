"""
AI assistant layer — natural language → structured operations.

Talks to any OpenAI-compatible chat-completions endpoint. The default is
a local Ollama server, so the whole feature runs free of charge on the
user's own machine:

    LLM_BASE_URL  (default http://localhost:11434/v1)
    LLM_MODEL     (default qwen2.5:3b — fast on ordinary laptops;
                   qwen2.5:7b gives better answers if you have ≥16 GB RAM)
    LLM_API_KEY   (default "ollama"; set a real key for hosted providers)

The same three variables also work with Groq, OpenRouter, LM Studio,
llama.cpp server, or any other endpoint that speaks the OpenAI protocol.

The model never edits data. It returns {"reply", "operations"}; the
operations are validated by app.operations and shown to the user for
confirmation before anything changes.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from scheduler.data import DAYS, tick_label

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LLM_MODEL    = os.environ.get("LLM_MODEL", "qwen2.5:3b")
LLM_API_KEY  = os.environ.get("LLM_API_KEY", "ollama")
LLM_TIMEOUT  = float(os.environ.get("LLM_TIMEOUT", "180"))


class AssistantError(Exception):
    pass


# ── prompt building ───────────────────────────────────────────────────────────

OPERATIONS_DOC = """\
Available operations (the "op" field selects one):

add_teacher     {"op":"add_teacher","name":str,"home":location,"qualified_levels":[level,...],"available_days":[day,...]}
update_teacher  {"op":"update_teacher","teacher":id_or_name, then any of:
                 "available_days":[day,...], "add_available_days":[day,...], "remove_available_days":[day,...],
                 "qualified_levels":[level,...], "home":location, "name":str,
                 "blocked_windows":[window,...], "add_blocked_windows":[window,...]}
remove_teacher  {"op":"remove_teacher","teacher":id_or_name}
add_room        {"op":"add_room","name":str,"location":location,"capacity":int}
update_room     {"op":"update_room","room":id_or_name, then any of: "name","location","capacity" (capacity=null → unlimited)}
remove_room     {"op":"remove_room","room":id_or_name}
add_class       {"op":"add_class","name":str,"level":level,"periods_per_session":int,"sessions_per_week":int,"preferred_location":location,"saturday_preferred":bool}
update_class    {"op":"update_class","class":id_or_name, then any of: "level","periods_per_session","sessions_per_week","preferred_location","name","saturday_preferred","pinned_teacher" (fix one teacher to the class; null unpins)}
remove_class    {"op":"remove_class","class":id_or_name}
add_student     {"op":"add_student","name":str,"class":id_or_name,"sibling_group":str_or_null,"blocked_windows":[window,...],"note":str}
update_student  {"op":"update_student","student":id_or_name, then any of: "class","sibling_group","blocked_windows","add_blocked_windows","note","name"}
remove_student  {"op":"remove_student","student":id_or_name}
set_day_hours   {"op":"set_day_hours","day":day,"location":location,"open":"HH:MM","close":"HH:MM"}  — or {"closed":true} to close
update_settings {"op":"update_settings","travel_periods":int} and/or {"young_learner_cutoff":"HH:MM"}

Value formats:
  day    = "Mon" … "Sat"
  time   = 24-hour "HH:MM", e.g. "17:30"
  window = {"day":"Tue","from":"16:00","to":"18:00"}  (a time range someone is NOT available)
"""

FEW_SHOT = [
    {"role": "user", "content": "Maria can't work on Tuesdays anymore"},
    {"role": "assistant", "content": json.dumps({
        "reply": "I'll remove Tuesday from Maria's available days. Review and apply, then re-solve.",
        "operations": [{"op": "update_teacher", "teacher": "Maria",
                        "remove_available_days": ["Tue"]}],
    })},
    {"role": "user", "content": "Η Άννα δεν μπορεί πια Παρασκευές, και έχει φροντιστήριο κάθε Δευτέρα 4 με 6"},
    {"role": "assistant", "content": json.dumps({
        "reply": "Θα αφαιρέσω την Παρασκευή από τις διαθέσιμες ημέρες της Άννας και θα προσθέσω "
                 "μη διαθεσιμότητα Δευτέρα 16:00-18:00. Ελέγξτε και εφαρμόστε.",
        "operations": [{"op": "update_teacher", "teacher": "Anna",
                        "remove_available_days": ["Fri"],
                        "add_blocked_windows": [{"day": "Mon", "from": "16:00", "to": "18:00"}]}],
    }, ensure_ascii=False)},
    {"role": "user", "content": "Νέα μαθήτρια Ελένη στο B2 Gr.1, έχει χορό Τετάρτες 5–7μμ"},
    {"role": "assistant", "content": json.dumps({
        "reply": "Θα προσθέσω την Ελένη στο B2 Gr.1 με μη διαθεσιμότητα Τετάρτη 17:00-19:00 "
                 "(χορός). Ελέγξτε και εφαρμόστε.",
        "operations": [{"op": "add_student", "name": "Ελένη", "class": "B2 Gr.1",
                        "blocked_windows": [{"day": "Wed", "from": "17:00", "to": "19:00"}],
                        "note": "Χορός Τετάρτη 17:00-19:00"}],
    }, ensure_ascii=False)},
    {"role": "user", "content": "how many teachers do we have?"},
    {"role": "assistant", "content": json.dumps({
        "reply": "You currently have 10 teachers on staff (see the summary in my context: T1-T10).",
        "operations": [],
    })},
]


def _summarise_school(school: dict) -> str:
    lines = ["CURRENT SCHOOL DATA", ""]

    lines.append("Locations: " + "; ".join(f"{k} = {v}" for k, v in school["locations"].items()))

    lines.append("Opening hours:")
    for day in DAYS:
        locs = school["day_hours"].get(day, {})
        parts = []
        for loc_id in school["locations"]:
            w = locs.get(loc_id)
            parts.append(f"{loc_id}: closed" if not w
                         else f"{loc_id}: {tick_label(w['open'])}-{tick_label(w['close'])}")
        lines.append(f"  {day}  " + " | ".join(parts))

    st = school["settings"]
    lines.append(f"Levels: {', '.join(st['levels'])}")
    lines.append(f"Young-learner levels (must end by {tick_label(st['young_learner_cutoff'])}): "
                 f"{', '.join(st['young_learner_levels'])}")
    lines.append(f"Travel gap between branches: {st['travel_periods']} periods")

    lines.append("Rooms:")
    for r in school["rooms"]:
        cap = f", capacity {r['capacity']}" if r.get("capacity") else ""
        lines.append(f"  {r['id']} {r['name']} @ {r['location']}{cap}")

    lines.append("Teachers:")
    for t in school["teachers"]:
        days = ",".join(DAYS[d] for d in t["available_days"])
        blocked = "; blocked: " + ", ".join(
            f"{DAYS[w[0]]} {tick_label(w[1])}-{tick_label(w[2])}" for w in t["blocked_windows"]
        ) if t.get("blocked_windows") else ""
        lines.append(f"  {t['id']} {t['name']} (home {t['home']}; days {days}; "
                     f"levels: {', '.join(t['qualified_levels'])}{blocked})")

    lines.append("Classes:")
    for c in school["classes"]:
        lines.append(f"  {c['id']} {c['name']} — {c['level']}, "
                     f"{c['periods_per_session']}p × {c['sessions_per_week']}/wk, "
                     f"prefers {c.get('preferred_location') or '-'}")

    lines.append("Students:")
    for s in school["students"]:
        extra = []
        if s.get("sibling_group"):
            extra.append(f"siblings {s['sibling_group']}")
        for w in s.get("blocked_windows", []):
            extra.append(f"blocked {DAYS[w[0]]} {tick_label(w[1])}-{tick_label(w[2])}")
        if s.get("note"):
            extra.append(s["note"])
        lines.append(f"  {s['id']} {s['name']} in {s['class_id']}"
                     + (f" ({'; '.join(extra)})" if extra else ""))

    return "\n".join(lines)


def _system_prompt(school: dict, schedule_summary: str | None) -> str:
    parts = [
        "You are the scheduling assistant of a language school. The timetable "
        "itself is computed by a constraint solver — you never invent schedules. "
        "Your job is to translate the manager's requests into structured "
        "operations on the school data, or to answer questions about the data.",
        "",
        "Respond with ONLY a JSON object, no markdown, in this exact shape:",
        '{"reply": "<short message to the manager>", "operations": [<zero or more operations>]}',
        "",
        "Rules:",
        "- The manager may write in Greek or English. ALWAYS write \"reply\" in the "
        "same language as the manager's message (Greek request → Greek reply).",
        "- Greek day names map EXACTLY as: Δευτέρα=Mon, Τρίτη=Tue, Τετάρτη=Wed, "
        "Πέμπτη=Thu, Παρασκευή=Fri, Σάββατο=Sat. (Τετάρτη is Wednesday, NOT Thursday. "
        "'5-7μμ' means 17:00-19:00.)",
        "- If a student/teacher/class named in the request does NOT appear in the "
        "CURRENT SCHOOL DATA below, use add_student/add_teacher/add_class — "
        "never update_* or remove_* on someone who doesn't exist.",
        "- Use set_day_hours ONLY when the manager explicitly asks to change a "
        "building's opening hours — never as part of a student or teacher change.",
        "- If the request is a question or chit-chat, return \"operations\": [].",
        "- Only use operations from the list below; never invent op names or fields.",
        "- Refer to teachers/classes/students by their id when possible.",
        "- If a request is ambiguous, ask for clarification in \"reply\" and return no operations.",
        "- Blocked windows mean NOT available at that time.",
        "- The operations are previewed to the manager and applied only after confirmation.",
        "",
        OPERATIONS_DOC,
        "",
        _summarise_school(school),
    ]
    if schedule_summary:
        parts += ["", "CURRENT SOLVED SCHEDULE", schedule_summary]
    return "\n".join(parts)


def summarise_schedule(result: dict | None) -> str | None:
    if not result:
        return None
    if not result.get("schedule"):
        if not result.get("status"):
            return None
        # infeasible / failed solve: pass the diagnosis to the model so it
        # can discuss the conflict with the user
        lines = [f"The last solve FAILED with status {result['status']}."]
        lines += [f"  Finding: {w}" for w in result.get("warnings", [])]
        return "\n".join(lines)
    lines = [f"Status {result['status']}, penalty {result.get('objective')}"]
    for e in result["schedule"]:
        lines.append(f"  {e['day']} {e['start_label']}-{e['end_label']} {e['class_name']} "
                     f"in {e['room_name']} ({e['location']}) with {e['teacher']}")
    return "\n".join(lines)


# ── LLM call ──────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Parse the model output, tolerating markdown fences and stray prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # last resort: first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise AssistantError(f"Model did not return valid JSON. Raw output:\n{text[:800]}")


def chat(school: dict, message: str, history: list[dict] | None = None,
         schedule: dict | None = None) -> dict:
    """→ {"reply": str, "operations": list} (operations not yet validated)."""
    messages = [{"role": "system", "content": _system_prompt(school, summarise_schedule(schedule))}]
    messages += FEW_SHOT
    for h in (history or [])[-10:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:4000]})
    messages.append({"role": "user", "content": message})

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    try:
        resp = httpx.post(
            f"{LLM_BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=LLM_TIMEOUT,
        )
    except httpx.ConnectError:
        raise AssistantError(
            f"Δεν βρέθηκε γλωσσικό μοντέλο στο {LLM_BASE_URL}. Τρέχει το Ollama; "
            f"Εγκατάσταση από https://ollama.com και μετά: `ollama pull {LLM_MODEL}`."
        )
    except httpx.TimeoutException:
        raise AssistantError(
            f"Το μοντέλο στο {LLM_BASE_URL} δεν απάντησε εντός {LLM_TIMEOUT:.0f}s. "
            "Ένα μικρότερο μοντέλο (π.χ. qwen2.5:3b) ίσως απαντά ταχύτερα σε αυτό το μηχάνημα."
        )

    if resp.status_code == 404:
        raise AssistantError(
            f"Το μοντέλο '{LLM_MODEL}' δεν βρέθηκε στον διακομιστή. "
            f"Εκτελέστε `ollama pull {LLM_MODEL}` (ή ορίστε LLM_MODEL σε εγκατεστημένο μοντέλο)."
        )
    if resp.status_code != 200:
        raise AssistantError(f"Σφάλμα διακομιστή LLM {resp.status_code}: {resp.text[:500]}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise AssistantError(f"Unexpected LLM response shape: {resp.text[:500]}")

    parsed = _extract_json(content)
    return {
        "reply": str(parsed.get("reply", "")),
        "operations": parsed.get("operations", []) or [],
    }


def status() -> dict:
    """Check whether the configured LLM endpoint is reachable and the model exists."""
    info = {"base_url": LLM_BASE_URL, "model": LLM_MODEL,
            "reachable": False, "model_available": None, "models": [], "hint": None}
    try:
        resp = httpx.get(
            f"{LLM_BASE_URL}/models",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=5,
        )
        info["reachable"] = resp.status_code == 200
        if info["reachable"]:
            models = [m.get("id", "") for m in resp.json().get("data", [])]
            info["models"] = models
            info["model_available"] = any(
                m == LLM_MODEL or m.split(":")[0] == LLM_MODEL.split(":")[0] for m in models
            )
            if info["model_available"] is False:
                info["hint"] = f"Εκτελέστε: ollama pull {LLM_MODEL}"
    except httpx.HTTPError:
        info["hint"] = ("Δεν βρέθηκε διακομιστής LLM. Εγκαταστήστε το Ollama "
                        f"(https://ollama.com) και εκτελέστε: ollama pull {LLM_MODEL}")
    return info
