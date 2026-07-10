/* Language School Scheduler — single-page UI */
"use strict";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const $ = (sel) => document.querySelector(sel);

let school = null;          // current school dict (mirrors server)
let dirty = false;
let chatHistory = [];       // [{role, content}] for assistant context

/* ── small helpers ─────────────────────────────────────────────── */

const tickLabel = (t) => `${String(Math.floor(t / 4)).padStart(2, "0")}:${String((t % 4) * 15).padStart(2, "0")}`;

function parseTimeStr(s) {
  const m = /^(\d{1,2})(?::(\d{2}))?$/.exec(s.trim());
  if (!m) return null;
  const t = parseInt(m[1], 10) * 4 + Math.floor(parseInt(m[2] || "0", 10) / 15);
  return t >= 0 && t <= 96 ? t : null;
}

/* "Mon 16:00-18:00; Wed 17:00-19:00" ↔ [[0,64,72], …] */
function parseWindowsText(text) {
  const out = [];
  for (const seg of text.split(";")) {
    const s = seg.trim();
    if (!s) continue;
    const m = /^(\w+)\s+(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})$/.exec(s);
    if (!m) return null;
    const d = DAYS.findIndex((x) => m[1].toLowerCase().startsWith(x.toLowerCase()));
    const o = parseTimeStr(m[2]), c = parseTimeStr(m[3]);
    if (d < 0 || o === null || c === null || o >= c) return null;
    out.push([d, o, c]);
  }
  return out;
}
const windowsToText = (ws) => (ws || []).map((w) => `${DAYS[w[0]]} ${tickLabel(w[1])}-${tickLabel(w[2])}`).join("; ");

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = body.detail;
    const msg = typeof detail === "string" ? detail
      : detail && detail.errors ? detail.errors.join("\n")
      : `Request failed (${resp.status})`;
    throw new Error(msg);
  }
  return body;
}

function setDirty(v) {
  dirty = v;
  $("#data-dirty").classList.toggle("hidden", !v);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ── tabs ──────────────────────────────────────────────────────── */

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});
function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
}

/* ── schedule rendering ────────────────────────────────────────── */

function renderStatus(result) {
  const bar = $("#solve-status");
  bar.classList.remove("hidden");
  bar.className = `status-bar status-${result.status || "UNKNOWN"}`;
  const pen = result.objective !== null && result.objective !== undefined
    ? `<span>Penalty: <b>${result.objective}</b> (0 = all preferences met)</span>` : "";
  const warns = (result.warnings || []).map((w) => `<div class="warn-line">⚠ ${esc(w)}</div>`).join("");
  const exports = (result.schedule || []).length
    ? `<span class="export-links"><a href="/api/export/pdf">⬇ PDF</a>
       <a href="/api/export/ics" title="Import into Google Calendar / Outlook / Apple Calendar">⬇ Calendar (.ics)</a></span>`
    : "";
  bar.innerHTML = `<span class="big">${esc(result.status || "—")}</span>
    <span>${(result.schedule || []).length} sessions</span> ${pen} ${exports} ${warns}`;
}

function renderSchedule(result) {
  renderStatus(result);
  const host = $("#schedule-grids");
  host.innerHTML = "";
  const sched = result.schedule || [];
  if (!sched.length) {
    host.innerHTML = `<p class="empty-note">No sessions scheduled.</p>`;
    return;
  }

  const locIds = Object.keys(school.locations);
  const rooms = [...school.rooms].sort((a, b) =>
    locIds.indexOf(a.location) - locIds.indexOf(b.location) || a.name.localeCompare(b.name));

  for (let day = 0; day < DAYS.length; day++) {
    const entries = sched.filter((e) => e.day_idx === day);
    if (!entries.length) continue;

    let open = Math.min(...entries.map((e) => e.start_tick));
    let close = Math.max(...entries.map((e) => e.end_tick));
    for (const loc of locIds) {
      const w = (school.day_hours[DAYS[day]] || {})[loc];
      if (w) { open = Math.min(open, w.open); close = Math.max(close, w.close); }
    }
    open = Math.floor(open / 4) * 4;
    close = Math.ceil(close / 4) * 4;
    const nticks = close - open;

    const card = document.createElement("div");
    card.className = "day-card";
    card.innerHTML = `<h2>${DAYS[day]}</h2>`;
    const grid = document.createElement("div");
    grid.className = "day-grid";
    grid.style.gridTemplateColumns = `56px repeat(${rooms.length}, minmax(110px, 1fr))`;
    grid.style.gridTemplateRows = `26px repeat(${nticks}, 7px)`;

    // header row (explicit columns — auto-placement would misalign them)
    grid.insertAdjacentHTML("beforeend", `<div class="grid-head" style="grid-row:1;grid-column:1"></div>`);
    rooms.forEach((r, i) => {
      grid.insertAdjacentHTML("beforeend",
        `<div class="grid-head" style="grid-row:1;grid-column:${i + 2}">${esc(r.name)} <small>(${esc(r.location)})</small></div>`);
    });

    // hour lines + labels
    for (let t = open; t <= close; t += 4) {
      const row = t - open + 2;
      grid.insertAdjacentHTML("beforeend",
        `<div class="time-label" style="grid-row:${row};grid-column:1">${tickLabel(t)}</div>`);
      if (t < close) {
        grid.insertAdjacentHTML("beforeend",
          `<div class="grid-line" style="grid-row:${row}"></div>`);
      }
    }

    // session blocks
    for (const e of entries) {
      const col = rooms.findIndex((r) => r.id === e.room_id) + 2;
      const locCls = `loc-${locIds.indexOf(e.location)}`;
      const div = document.createElement("div");
      div.className = `session ${locCls}`;
      div.style.gridColumn = col;
      div.style.gridRow = `${e.start_tick - open + 2} / ${e.end_tick - open + 2}`;
      div.title = `${e.class_name} — ${e.teacher}, ${e.start_label}–${e.end_label}`;
      div.innerHTML = `<div class="cls">${esc(e.class_name)}</div>
        <div class="meta">${e.start_label}–${e.end_label} · ${esc(e.teacher)}</div>`;
      grid.appendChild(div);
    }

    card.appendChild(grid);
    host.appendChild(card);
  }
}

$("#solve-btn").addEventListener("click", async () => {
  const btn = $("#solve-btn");
  if (dirty && !confirm("You have unsaved data changes — solve with the last saved data anyway?")) return;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner">⚙</span> Solving…`;
  try {
    const result = await api("/api/solve", { method: "POST", body: JSON.stringify({}) });
    renderSchedule(result);
    switchTab("schedule");
  } catch (e) {
    alert("Solve failed:\n" + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "⚙ Solve";
  }
});

/* ── data editors ──────────────────────────────────────────────── */

function renderData() {
  renderHours();
  renderTeachers();
  renderRooms();
  renderClasses();
  renderStudents();
}

function locOptions(selected, allowEmpty) {
  let html = allowEmpty ? `<option value="" ${!selected ? "selected" : ""}>—</option>` : "";
  for (const [id, name] of Object.entries(school.locations)) {
    html += `<option value="${esc(id)}" ${id === selected ? "selected" : ""}>${esc(id)} — ${esc(name)}</option>`;
  }
  return html;
}
const levelOptions = (selected) => school.settings.levels.map(
  (lv) => `<option ${lv === selected ? "selected" : ""}>${esc(lv)}</option>`).join("");

function renderHours() {
  const locIds = Object.keys(school.locations);
  let html = `<table class="editor"><tr><th>Day</th>${locIds.map((l) => `<th>${esc(l)} — ${esc(school.locations[l])}</th>`).join("")}</tr>`;
  for (const day of DAYS) {
    html += `<tr><td><b>${day}</b></td>`;
    for (const loc of locIds) {
      const w = (school.day_hours[day] || {})[loc];
      const val = w ? `${tickLabel(w.open)}-${tickLabel(w.close)}` : "";
      html += `<td><input type="text" data-day="${day}" data-loc="${esc(loc)}" value="${val}" placeholder="closed"></td>`;
    }
    html += `</tr>`;
  }
  html += `</table>`;
  const host = $("#hours-editor");
  host.innerHTML = html;
  host.querySelectorAll("input").forEach((inp) => inp.addEventListener("change", () => {
    const { day, loc } = inp.dataset;
    const v = inp.value.trim();
    school.day_hours[day] = school.day_hours[day] || {};
    if (!v) { school.day_hours[day][loc] = null; setDirty(true); return; }
    const m = /^(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})$/.exec(v);
    const o = m && parseTimeStr(m[1]), c = m && parseTimeStr(m[2]);
    if (!m || o === null || c === null || o >= c) {
      alert(`Invalid hours "${v}" — use e.g. 15:30-21:00 or leave empty.`);
      renderHours(); return;
    }
    school.day_hours[day][loc] = { open: o, close: c };
    setDirty(true);
  }));
}

function dayChecks(days, idx, kind) {
  return `<div class="day-checks">` + DAYS.map((d, i) =>
    `<label>${d[0]}<input type="checkbox" data-kind="${kind}" data-idx="${idx}" data-day="${i}"
      ${days.includes(i) ? "checked" : ""}></label>`).join("") + `</div>`;
}

function renderTeachers() {
  let html = `<table class="editor"><tr>
    <th>Id</th><th>Name</th><th>Home</th><th>Qualified levels</th><th>Days</th><th>Blocked times</th><th></th></tr>`;
  school.teachers.forEach((t, i) => {
    html += `<tr>
      <td>${esc(t.id)}</td>
      <td><input type="text" data-f="name" data-i="${i}" value="${esc(t.name)}"></td>
      <td><select data-f="home" data-i="${i}">${locOptions(t.home)}</select></td>
      <td><input type="text" data-f="levels" data-i="${i}" value="${esc(t.qualified_levels.join(", "))}"></td>
      <td>${dayChecks(t.available_days, i, "teacher")}</td>
      <td><input type="text" data-f="blocked" data-i="${i}" value="${esc(windowsToText(t.blocked_windows))}" placeholder="Mon 16:00-18:00"></td>
      <td><button class="row-del" data-i="${i}" title="Remove">✕</button></td></tr>`;
  });
  html += `</table>`;
  const host = $("#teachers-editor");
  host.innerHTML = html;

  host.querySelectorAll("input[type=text], select").forEach((el) => el.addEventListener("change", () => {
    const t = school.teachers[+el.dataset.i];
    const f = el.dataset.f;
    if (f === "name") t.name = el.value.trim();
    else if (f === "home") t.home = el.value;
    else if (f === "levels") t.qualified_levels = el.value.split(",").map((s) => s.trim()).filter(Boolean);
    else if (f === "blocked") {
      const ws = parseWindowsText(el.value);
      if (ws === null) { alert("Invalid blocked-times format. Use: Mon 16:00-18:00; Wed 17:00-19:00"); renderTeachers(); return; }
      t.blocked_windows = ws;
    }
    setDirty(true);
  }));
  host.querySelectorAll("input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
    const t = school.teachers[+cb.dataset.idx];
    const d = +cb.dataset.day;
    t.available_days = cb.checked
      ? [...new Set([...t.available_days, d])].sort()
      : t.available_days.filter((x) => x !== d);
    setDirty(true);
  }));
  host.querySelectorAll(".row-del").forEach((b) => b.addEventListener("click", () => {
    const t = school.teachers[+b.dataset.i];
    if (!confirm(`Remove teacher ${t.name}?`)) return;
    school.teachers.splice(+b.dataset.i, 1);
    setDirty(true); renderTeachers();
  }));
}

function renderRooms() {
  let html = `<table class="editor"><tr><th>Id</th><th>Name</th><th>Location</th><th></th></tr>`;
  school.rooms.forEach((r, i) => {
    html += `<tr><td>${esc(r.id)}</td>
      <td><input type="text" data-f="name" data-i="${i}" value="${esc(r.name)}"></td>
      <td><select data-f="location" data-i="${i}">${locOptions(r.location)}</select></td>
      <td><button class="row-del" data-i="${i}">✕</button></td></tr>`;
  });
  const host = $("#rooms-editor");
  host.innerHTML = html + `</table>`;
  host.querySelectorAll("input, select").forEach((el) => el.addEventListener("change", () => {
    school.rooms[+el.dataset.i][el.dataset.f] = el.value.trim();
    setDirty(true);
  }));
  host.querySelectorAll(".row-del").forEach((b) => b.addEventListener("click", () => {
    if (!confirm(`Remove room ${school.rooms[+b.dataset.i].name}?`)) return;
    school.rooms.splice(+b.dataset.i, 1);
    setDirty(true); renderRooms();
  }));
}

function renderClasses() {
  let html = `<table class="editor"><tr>
    <th>Id</th><th>Name</th><th>Level</th><th>Periods/session</th><th>Sessions/week</th><th>Preferred loc.</th><th title="Prefer a Saturday slot">Sat pref.</th><th></th></tr>`;
  school.classes.forEach((c, i) => {
    html += `<tr><td>${esc(c.id)}</td>
      <td><input type="text" data-f="name" data-i="${i}" value="${esc(c.name)}"></td>
      <td><select data-f="level" data-i="${i}">${levelOptions(c.level)}</select></td>
      <td><input type="number" min="1" max="6" class="narrow" data-f="periods_per_session" data-i="${i}" value="${c.periods_per_session}"></td>
      <td><select data-f="sessions_per_week" data-i="${i}">
        ${[1, 2, 3].map((n) => `<option ${n === c.sessions_per_week ? "selected" : ""}>${n}</option>`).join("")}</select></td>
      <td><select data-f="preferred_location" data-i="${i}">${locOptions(c.preferred_location, true)}</select></td>
      <td><input type="checkbox" data-f="saturday_preferred" data-i="${i}" ${c.saturday_preferred ? "checked" : ""}></td>
      <td><button class="row-del" data-i="${i}">✕</button></td></tr>`;
  });
  const host = $("#classes-editor");
  host.innerHTML = html + `</table>`;
  host.querySelectorAll("input, select").forEach((el) => el.addEventListener("change", () => {
    const c = school.classes[+el.dataset.i];
    const f = el.dataset.f;
    if (f === "periods_per_session" || f === "sessions_per_week") c[f] = parseInt(el.value, 10) || 1;
    else if (f === "preferred_location") c[f] = el.value || null;
    else if (f === "saturday_preferred") c[f] = el.checked;
    else c[f] = el.value.trim();
    setDirty(true);
  }));
  host.querySelectorAll(".row-del").forEach((b) => b.addEventListener("click", () => {
    const c = school.classes[+b.dataset.i];
    if (!confirm(`Remove class ${c.name} (and its students' assignments)?`)) return;
    school.classes.splice(+b.dataset.i, 1);
    school.students = school.students.filter((s) => s.class_id !== c.id);
    setDirty(true); renderClasses(); renderStudents();
  }));
}

function renderStudents() {
  const clsOptions = (sel) => school.classes.map(
    (c) => `<option value="${esc(c.id)}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  let html = `<table class="editor"><tr>
    <th>Id</th><th>Name</th><th>Class</th><th>Sibling group</th><th>Blocked times</th><th>Note</th><th></th></tr>`;
  school.students.forEach((s, i) => {
    html += `<tr><td>${esc(s.id)}</td>
      <td><input type="text" data-f="name" data-i="${i}" value="${esc(s.name)}"></td>
      <td><select data-f="class_id" data-i="${i}">${clsOptions(s.class_id)}</select></td>
      <td><input type="text" class="narrow" data-f="sibling_group" data-i="${i}" value="${esc(s.sibling_group || "")}" placeholder="—"></td>
      <td><input type="text" data-f="blocked" data-i="${i}" value="${esc(windowsToText(s.blocked_windows))}" placeholder="Mon 16:00-18:00"></td>
      <td><input type="text" data-f="note" data-i="${i}" value="${esc(s.note || "")}"></td>
      <td><button class="row-del" data-i="${i}">✕</button></td></tr>`;
  });
  const host = $("#students-editor");
  host.innerHTML = html + `</table>`;
  host.querySelectorAll("input, select").forEach((el) => el.addEventListener("change", () => {
    const s = school.students[+el.dataset.i];
    const f = el.dataset.f;
    if (f === "blocked") {
      const ws = parseWindowsText(el.value);
      if (ws === null) { alert("Invalid blocked-times format. Use: Mon 16:00-18:00; Wed 17:00-19:00"); renderStudents(); return; }
      s.blocked_windows = ws;
    } else if (f === "sibling_group") s.sibling_group = el.value.trim() || null;
    else s[f] = el.value.trim();
    setDirty(true);
  }));
  host.querySelectorAll(".row-del").forEach((b) => b.addEventListener("click", () => {
    if (!confirm(`Remove student ${school.students[+b.dataset.i].name}?`)) return;
    school.students.splice(+b.dataset.i, 1);
    setDirty(true); renderStudents();
  }));
}

function nextId(items, prefix) {
  let mx = 0;
  for (const it of items) {
    const m = new RegExp(`^${prefix}(\\d+)$`, "i").exec(it.id || "");
    if (m) mx = Math.max(mx, parseInt(m[1], 10));
  }
  return `${prefix}${mx + 1}`;
}

document.querySelectorAll(".add-btn").forEach((btn) => btn.addEventListener("click", () => {
  const firstLoc = Object.keys(school.locations)[0];
  switch (btn.dataset.add) {
    case "teacher":
      school.teachers.push({ id: nextId(school.teachers, "T"), name: "New teacher", home: firstLoc,
        qualified_levels: [], available_days: [0, 1, 2, 3, 4, 5], blocked_windows: [] });
      renderTeachers(); break;
    case "room":
      school.rooms.push({ id: nextId(school.rooms, "R"), name: "New room", location: firstLoc });
      renderRooms(); break;
    case "class":
      school.classes.push({ id: nextId(school.classes, "C"), name: "New class",
        level: school.settings.levels[0], periods_per_session: 2, sessions_per_week: 2,
        preferred_location: firstLoc });
      renderClasses(); break;
    case "student": {
      if (!school.classes.length) { alert("Add a class first."); return; }
      school.students.push({ id: nextId(school.students, "S"), name: "New student",
        class_id: school.classes[0].id, sibling_group: null, blocked_windows: [], note: "" });
      renderStudents(); break;
    }
  }
  setDirty(true);
}));

$("#save-btn").addEventListener("click", async () => {
  const errBox = $("#data-errors");
  errBox.classList.add("hidden");
  try {
    await api("/api/school", { method: "PUT", body: JSON.stringify(school) });
    setDirty(false);
  } catch (e) {
    errBox.textContent = "Cannot save:\n" + e.message;
    errBox.classList.remove("hidden");
  }
});

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Discard ALL data and restore the sample school?")) return;
  const body = await api("/api/school/reset", { method: "POST" });
  school = body.school;
  setDirty(false);
  renderData();
  $("#schedule-grids").innerHTML = `<p class="empty-note">Data was reset — press <b>Solve</b> to generate a new timetable.</p>`;
  $("#solve-status").classList.add("hidden");
});

/* ── assistant ─────────────────────────────────────────────────── */

async function checkAI() {
  const pill = $("#ai-pill");
  const banner = $("#ai-banner");
  try {
    const st = await api("/api/assistant/status");
    if (st.reachable && st.model_available !== false) {
      pill.className = "pill pill-ok";
      pill.textContent = `AI: ${st.model}`;
      banner.classList.add("hidden");
    } else if (st.reachable) {
      pill.className = "pill pill-bad";
      pill.textContent = "AI: model missing";
      banner.textContent = `The server at ${st.base_url} is running but model "${st.model}" is not installed. ${st.hint || ""}`;
      banner.classList.remove("hidden");
    } else {
      pill.className = "pill pill-bad";
      pill.textContent = "AI: offline";
      banner.innerHTML = `No local AI found at <b>${esc(st.base_url)}</b>. The assistant needs a free local model:
        install <a href="https://ollama.com" target="_blank">Ollama</a>, then run
        <code>ollama pull ${esc(st.model)}</code>. Everything else in the app works without it.`;
      banner.classList.remove("hidden");
    }
  } catch {
    pill.className = "pill pill-unknown";
    pill.textContent = "AI: unknown";
  }
}

function addMsg(cls, html) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  $("#chat-log").appendChild(div);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return div;
}

function addProposal(operations, preview, errors) {
  const card = document.createElement("div");
  card.className = "proposal";
  const okToApply = !errors.length && operations.length;
  card.innerHTML = `<h3>Proposed changes</h3>
    <ul>${preview.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
    ${errors.length ? `<div class="op-errors">⚠ ${errors.map(esc).join("<br>⚠ ")}</div>` : ""}
    <div class="actions">
      ${okToApply ? `<button class="primary" data-act="apply-solve">✓ Apply &amp; re-solve</button>
                     <button class="primary" data-act="apply">Apply only</button>` : ""}
      <button class="ghost" data-act="dismiss">Dismiss</button>
    </div>`;
  $("#chat-log").appendChild(card);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;

  card.querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
    const act = btn.dataset.act;
    if (act === "dismiss") { card.classList.add("done"); card.querySelector(".actions").remove(); return; }
    btn.disabled = true;
    try {
      const body = await api("/api/assistant/apply", {
        method: "POST",
        body: JSON.stringify({ operations, resolve: act === "apply-solve" }),
      });
      school = body.school;
      renderData();
      setDirty(false);
      card.classList.add("done");
      card.querySelector(".actions").remove();
      if (body.result) {
        renderSchedule(body.result);
        addMsg("system", `Applied ✓ — re-solved: ${esc(body.result.status)}${
          body.result.objective !== null ? `, penalty ${body.result.objective}` : ""}. See the Schedule tab.`);
      } else {
        addMsg("system", "Applied ✓ — press Solve to refresh the timetable.");
      }
    } catch (e) {
      btn.disabled = false;
      addMsg("error", `Could not apply: ${esc(e.message)}`);
    }
  }));
}

$("#chat-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMsg("user", esc(text));
  chatHistory.push({ role: "user", content: text });

  const thinking = addMsg("assistant", `<span class="spinner">🤔</span> thinking…`);
  $("#chat-send").disabled = true;
  try {
    const body = await api("/api/assistant/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, history: chatHistory.slice(0, -1) }),
    });
    thinking.innerHTML = esc(body.reply || "(no reply)");
    chatHistory.push({ role: "assistant", content: body.reply });
    if (body.operations.length || body.errors.length) {
      addProposal(body.operations, body.preview, body.errors);
    }
  } catch (e) {
    thinking.remove();
    addMsg("error", esc(e.message));
  } finally {
    $("#chat-send").disabled = false;
    input.focus();
  }
});

/* ── init ──────────────────────────────────────────────────────── */

(async function init() {
  school = await api("/api/school");
  renderData();
  const last = await api("/api/schedule");
  if (last && last.schedule && last.schedule.length) renderSchedule(last);
  checkAI();
})();
