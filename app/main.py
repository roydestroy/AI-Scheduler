"""
Language School Scheduler — REST API + web UI.

Run from the repo root:
    uvicorn app.main:app --reload

Then open http://localhost:8000
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scheduler.diagnose import diagnose
from scheduler.solver import preference_report, solve
from . import assistant, backup, diff, erp, export, operations, slots, store

app = FastAPI(title="Language School Scheduler", version="0.4")


# ── optional shared password (multi-PC / network deployments) ─────────────────
# Set APP_PASSWORD to require a login; leave unset for single-PC use.
# Transport security comes from the LAN/Tailscale, this keeps casual
# visitors (students on the school wifi…) out of the schedule data.

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_AUTH_COOKIE = "scheduler_auth"


def _auth_token() -> str:
    return hmac.new(APP_PASSWORD.encode(), b"scheduler-auth-v1", hashlib.sha256).hexdigest()


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if not APP_PASSWORD or request.url.path in ("/login", "/logout"):
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(_AUTH_COOKIE, ""), _auth_token()):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Απαιτείται σύνδεση — ανανεώστε τη σελίδα για να συνδεθείτε."},
                            status_code=401)
    return RedirectResponse("/login", status_code=303)


_LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Πρόγραμμα Σχολείου — Σύνδεση</title>
<style>
 body {{ font: 16px system-ui; background: #f5f6f8; display: flex;
        align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
 form {{ background: #fff; border: 1px solid #e3e6eb; border-radius: 14px;
        padding: 32px 36px; display: flex; flex-direction: column; gap: 14px; width: 300px; }}
 h1 {{ font-size: 18px; margin: 0; }}
 input {{ padding: 10px 12px; border: 1px solid #e3e6eb; border-radius: 8px; font-size: 15px; }}
 button {{ background: #2563eb; color: #fff; border: none; padding: 10px;
          border-radius: 8px; font-size: 15px; cursor: pointer; }}
 .err {{ color: #dc2626; font-size: 14px; margin: 0; }}
</style></head><body>
<form method="post" action="/login">
  <h1>🗓 Πρόγραμμα Σχολείου</h1>
  {error}
  <input type="password" name="password" placeholder="Κωδικός" autofocus>
  <button type="submit">Σύνδεση</button>
</form></body></html>"""


@app.get("/login")
def login_page(e: int = 0):
    err = '<p class="err">Λάθος κωδικός — δοκιμάστε ξανά.</p>' if e else ""
    return HTMLResponse(_LOGIN_PAGE.format(error=err))


@app.post("/login")
async def login_submit(request: Request):
    # parse the urlencoded body directly — avoids the python-multipart dependency
    from urllib.parse import parse_qs
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    if APP_PASSWORD and hmac.compare_digest(str(form.get("password", "")), APP_PASSWORD):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(_AUTH_COOKIE, _auth_token(), max_age=90 * 24 * 3600,
                        httponly=True, samesite="lax")
        return resp
    return RedirectResponse("/login?e=1", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(_AUTH_COOKIE)
    return resp


# ── request models ────────────────────────────────────────────────────────────

class SolveRequest(BaseModel):
    time_limit_seconds: int = Field(default=60, ge=1, le=600)


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    operations: list[dict]
    resolve: bool = False
    time_limit_seconds: int = Field(default=60, ge=1, le=600)


# ── workspaces & undo ─────────────────────────────────────────────────────────

class WorkspaceCreateRequest(BaseModel):
    name: str
    seed_from_active: bool = False


class WorkspaceActivateRequest(BaseModel):
    key: str


@app.get("/api/workspaces")
def get_workspaces():
    return store.list_workspaces()


@app.post("/api/workspaces")
def post_workspace(req: WorkspaceCreateRequest):
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="Workspace name is required.")
    ws = store.create_workspace(req.name.strip(), seed_from_active=req.seed_from_active)
    return {"ok": True, "created": ws, **store.list_workspaces()}


@app.post("/api/workspaces/activate")
def post_workspace_activate(req: WorkspaceActivateRequest):
    try:
        store.activate_workspace(req.key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, **store.list_workspaces()}


@app.get("/api/history")
def get_history():
    return {"items": store.history_list()}


@app.post("/api/undo")
def post_undo():
    snap = store.undo()
    if snap is None:
        raise HTTPException(status_code=404, detail="Δεν υπάρχει τίποτα για αναίρεση.")
    return {"ok": True, "label": snap["label"], "school": snap["school"],
            "result": snap.get("schedule"), "rev": store.school_rev()}


# ── school data ───────────────────────────────────────────────────────────────

class SchoolUpdate(BaseModel):
    school: dict
    base_rev: str | None = None


@app.get("/api/school")
def get_school():
    return {"school": store.load_school(), "rev": store.school_rev()}


@app.get("/api/rev")
def get_rev():
    return {"school_rev": store.school_rev(), "schedule_rev": store.schedule_rev()}


@app.put("/api/school")
def put_school(req: SchoolUpdate):
    if req.base_rev and req.base_rev != store.school_rev():
        raise HTTPException(
            status_code=409,
            detail="Κάποιος άλλος άλλαξε τα δεδομένα του σχολείου όσο κάνατε επεξεργασία. "
                   "Ανανεώστε για να πάρετε την τελευταία έκδοση "
                   "(οι μη αποθηκευμένες αλλαγές σας θα χαθούν).")
    errors = store.validate_school(req.school)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    try:
        backup.make_backup("save")
    except OSError:
        pass                     # backups are best-effort, never block a save
    store.push_history("Manual edit of school data")
    store.save_school(req.school)
    return {"ok": True, "school": req.school, "rev": store.school_rev()}


@app.post("/api/school/reset")
def reset_school():
    store.push_history("Reset to sample data")
    return {"ok": True, "school": store.reset_school(), "rev": store.school_rev()}


# ── solving ───────────────────────────────────────────────────────────────────

def _solve_and_store(school: dict, time_limit_seconds: int) -> dict:
    previous = store.load_last_schedule()
    if previous and previous.get("schedule"):
        # tie-break towards the previous solve so nothing moves without a
        # reason (weight-1 sticky_slots; not persisted — school is a copy)
        school = copy.deepcopy(school)
        store.attach_baseline(school, previous, field="sticky_slots")
    result = solve(school, time_limit_seconds=time_limit_seconds)
    if not result["schedule"]:
        # explain WHY it is unsolvable (static checks + relaxation probes)
        result["warnings"].extend(diagnose(school, time_limit_per_probe=10))
    # what changed vs the previous solve, so the user never loses track
    result["changes"] = diff.compute_changes(previous, result)
    # which soft preferences ended up unmet (drives the readable penalty view)
    if result["schedule"]:
        result["preferences"] = preference_report(school, result["schedule"])
    store.save_last_schedule(result)
    return result


@app.post("/api/solve")
def post_solve(req: SolveRequest = SolveRequest()):
    school = store.load_school()
    errors = store.validate_school(school)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    store.push_history("Re-solve of the schedule")
    return _solve_and_store(school, req.time_limit_seconds)


@app.get("/api/schedule")
def get_schedule():
    return store.load_last_schedule() or {"status": None, "schedule": [], "warnings": []}


# ── ERP import ────────────────────────────────────────────────────────────────

class ErpPeriodsRequest(BaseModel):
    source: str


class ErpPreviewRequest(BaseModel):
    source: str
    academic_period_id: str | None = None


class ErpApplyRequest(BaseModel):
    source: str
    mapping: dict
    academic_period_id: str | None = None


@app.get("/api/erp/sources")
def erp_sources():
    try:
        return {"sources": erp.public_sources()}
    except erp.ErpError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/erp/periods")
def erp_periods(req: ErpPeriodsRequest):
    try:
        return {"periods": erp.fetch_periods(erp.get_source(req.source))}
    except erp.ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/erp/preview")
def erp_preview(req: ErpPreviewRequest):
    try:
        source = erp.get_source(req.source)
        rows = erp.fetch_rows(source, req.academic_period_id)
        out = erp.build_plan(store.load_school(), source, rows,
                             erp.load_mapping())
    except erp.ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return out["plan"]


@app.post("/api/erp/apply")
def erp_apply(req: ErpApplyRequest):
    try:
        source = erp.get_source(req.source)
        rows = erp.fetch_rows(source, req.academic_period_id)
        out = erp.build_plan(store.load_school(), source, rows, req.mapping)
    except erp.ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))

    errors = store.validate_school(out["school"])
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    store.push_history(f"ERP import from {source['name']}")
    erp.save_mapping(out["plan"]["mapping"])
    store.save_school(out["school"])
    return {"ok": True, "plan": out["plan"], "school": out["school"],
            "rev": store.school_rev()}


# ── exports ───────────────────────────────────────────────────────────────────

def _last_schedule_or_404() -> dict:
    result = store.load_last_schedule()
    if not result or not result.get("schedule"):
        raise HTTPException(status_code=404,
                            detail="Δεν υπάρχει ακόμη πρόγραμμα — εκτελέστε πρώτα την επίλυση.")
    return result


@app.get("/api/export/ics")
def export_ics():
    result = _last_schedule_or_404()
    return Response(
        content=export.build_ics(result, store.load_school()),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="school-timetable.ics"'},
    )


@app.get("/api/export/pdf")
def export_pdf():
    result = _last_schedule_or_404()
    return Response(
        content=export.build_pdf(result, store.load_school()),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="school-timetable.pdf"'},
    )


@app.get("/api/export/slips")
def export_slips(by: str = "class"):
    """Parent notice slips (Greek PDF): one per class (by=class) or per
    student (by=student)."""
    result = _last_schedule_or_404()
    pdf = export.build_slips(result, store.load_school(), by=by)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="school-slips-{by}.pdf"'},
    )


# ── free-slot finder ──────────────────────────────────────────────────────────

@app.get("/api/slots")
def api_slots(kind: str, id: str, periods: int = 2):
    result = store.load_last_schedule() or {"schedule": []}
    return slots.find_slots(store.load_school(), result.get("schedule", []),
                            kind, id, periods=periods)


# ── backups ───────────────────────────────────────────────────────────────────

@app.get("/api/backup")
def api_backup_download():
    name, data = backup.download_bytes()
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/backups")
def api_backups_list():
    return {"backups": backup.list_backups()}


# ── what-if preview (dry-run re-solve, nothing saved) ─────────────────────────

@app.post("/api/whatif")
def api_whatif(req: ApplyRequest):
    school = store.load_school()
    new_school, preview, errors = operations.apply_operations(school, req.operations)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    previous = store.load_last_schedule()
    trial = copy.deepcopy(new_school)
    if previous and previous.get("schedule"):
        store.attach_baseline(trial, previous, field="sticky_slots")
    result = solve(trial, time_limit_seconds=req.time_limit_seconds)
    result["changes"] = diff.compute_changes(previous, result)
    if result["schedule"]:
        result["preferences"] = preference_report(new_school, result["schedule"])
    return {"preview": preview, "result": result}


# ── AI assistant ──────────────────────────────────────────────────────────────

@app.get("/api/assistant/status")
def assistant_status():
    return assistant.status()


@app.post("/api/assistant/chat")
def assistant_chat(req: ChatRequest):
    school = store.load_school()
    try:
        out = assistant.chat(school, req.message, req.history,
                             schedule=store.load_last_schedule())
    except assistant.AssistantError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Dry-run the proposed operations so the user sees a preview (or errors)
    _, preview, errors = operations.apply_operations(school, out["operations"])
    return {
        "reply": out["reply"],
        "operations": out["operations"],
        "preview": preview,
        "errors": errors,
    }


@app.post("/api/assistant/apply")
def assistant_apply(req: ApplyRequest):
    school = store.load_school()
    new_school, preview, errors = operations.apply_operations(school, req.operations)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    val_errors = store.validate_school(new_school)
    if val_errors:
        raise HTTPException(status_code=422, detail={"errors": val_errors})

    store.push_history("AI change: " + (preview[0] if preview else "assistant edit"))
    store.save_school(new_school)
    response = {"ok": True, "preview": preview, "school": new_school,
                "result": None, "rev": store.school_rev()}

    if req.resolve:
        response["result"] = _solve_and_store(new_school, req.time_limit_seconds)
    return response


# ── static UI (must be mounted last) ──────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
