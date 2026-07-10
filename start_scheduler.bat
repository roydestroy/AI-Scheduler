@echo off
REM ── Language School Scheduler — network launcher (Windows) ──────────────
REM Starts the app listening on ALL network interfaces, so other PCs
REM (the second branch over Tailscale, the front desk, ...) can open it
REM at  http://<this-pc's-ip>:8000
REM
REM One-time setup on this PC:
REM   1. Allow the port through the firewall (run once, as administrator):
REM      netsh advfirewall firewall add rule name="School Scheduler" dir=in action=allow protocol=TCP localport=8000
REM   2. Set a password below (recommended as soon as other PCs connect).

cd /d %~dp0
call .venv\Scripts\activate

REM Recommended: uncomment and choose a password shared with your staff.
REM set APP_PASSWORD=change-me

REM Optional overrides (defaults shown):
REM set LLM_MODEL=qwen2.5:3b
REM set LLM_BASE_URL=http://localhost:11434/v1

uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
