"""
AI assistant layer — natural language → structured operations.

Talks to any OpenAI-compatible chat-completions endpoint. The default is
a local Ollama server, so the whole feature runs free of charge on the
user's own machine:

    LLM_BASE_URL  (default http://localhost:11434/v1)
    LLM_MODEL     (default qwen2.5:7b — good at structured JSON output)
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
LLM_MODEL    = os.environ.get("LLM_MODEL", "qwen2.5:7b")
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
add_room        {"op":"add_room","name":str,"location":location}
remove_room     {"op":"remove_room","room":id_or_name}
add_class       {"op":"add_class","name":str,"level":level,"periods_per_session":int,"sessions_per_week":int,"preferred_location":location}
update_class    {"op":"update_class","class":id_or_name, then any of: "level","periods_per_session","sessions_per_week","preferred_location","name"}
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
    {"role": "user", "content": "New student Eleni joins B2 Gr.1, she has dance class on Wednesdays 5 to 7pm"},
    {"role": "assistant", "content": json.dumps({
        "reply": "Adding Eleni to B2 Gr.1 with a Wednesday 17:00-19:00 blocked window.",
        "operations": [{"op": "add_student", "name": "Eleni", "class": "B2 Gr.1",
                        "blocked_windows": [{"day": "Wed", "from": "17:00", "to": "19:00"}],
                        "note": "Dance class Wed 17:00-19:00"}],
    })},
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
        lines.append(f"  {r['id']} {r['name']} @ {r['location']}")

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
    if not result or not result.get("schedule"):
        return None
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
            f"Cannot reach the language model at {LLM_BASE_URL}. "
            "Is Ollama running? Install from https://ollama.com, then: "
            f"`ollama pull {LLM_MODEL}` and `ollama serve`."
        )
    except httpx.TimeoutException:
        raise AssistantError(
            f"The model at {LLM_BASE_URL} timed out after {LLM_TIMEOUT:.0f}s. "
            "A smaller model (e.g. qwen2.5:3b) may respond faster on this machine."
        )

    if resp.status_code == 404:
        raise AssistantError(
            f"Model '{LLM_MODEL}' not found on the server. "
            f"Run `ollama pull {LLM_MODEL}` (or set LLM_MODEL to an installed model)."
        )
    if resp.status_code != 200:
        raise AssistantError(f"LLM server error {resp.status_code}: {resp.text[:500]}")

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
                info["hint"] = f"Run: ollama pull {LLM_MODEL}"
    except httpx.HTTPError:
        info["hint"] = ("No LLM server found. Install Ollama (https://ollama.com), "
                        f"then run: ollama pull {LLM_MODEL}")
    return info
