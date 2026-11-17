// Smart Elevator System UI (Huy, HY-06). Plain JavaScript, no framework (PROJECT_PLAN.md).
//
// Reads the Snapshot (contracts/ui.py) from the WebSocket /ws. If the WebSocket is not
// available (An's requirements.txt has no WebSocket library yet, see ai-log/huy.md A1),
// it falls back to GET /snapshot every POLL_MS. Commands go to the API with the role
// chosen in the top bar; the API and the Control Center check the role, not this page.

"use strict";

// Copied from contracts/config.py (the browser cannot import Python). Keep in sync.
// The list of cars is NOT copied: scenarios use different fleets (AT-04 has only P1 and
// G1), so it always comes from the snapshot (see syncFleet).
const FLOORS = 40;
const FULL_LOAD_KG = 900;
const MAX_LOAD_KG = 1000;

const POLL_MS = 500;            // fallback refresh when /ws is not available
const TELEMETRY_STALE_S = 3.0;  // contracts/config.py: older telemetry makes a car ineligible
const TOAST_MS = 3500;
const EVENTS_SHOWN = 50;
const AUDIT_MS = 2000;          // how often the refused-requests list is refreshed
const AUDIT_SHOWN = 30;
const SERVED_SHOWN_S = 15;      // UI-03: how long (simulated seconds) a served destination stays green

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let lastSnapshot = null;

// --- helpers ------------------------------------------------------------------------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function fmt(value, digits = 1) {
  return value === null || value === undefined ? "-" : Number(value).toFixed(digits);
}

function seconds(value) {
  return value === null || value === undefined ? "no data yet" : `${fmt(value)} s`;
}

const ARROW = { UP: "▲", DOWN: "▼", IDLE: "·" };

// Floor f (1 = ground) as a distance from the top of the hoistway, in floors.
function fromTop(position) {
  return FLOORS - position;
}

function role() {
  return $("#role").value;
}

// Human wording for reasons coming back from the API (RejectReason values).
const REASON_TEXT = {
  UNAUTHORIZED_ROLE: "This role is not allowed to do that. Pick another role in the top bar.",
  MISSING_FIELD: "A required value is missing.",
  FLOOR_OUT_OF_RANGE: `Floors go from 1 to ${FLOORS}.`,
  SAME_ORIGIN_DESTINATION: "From and to floors must differ.",
  INVALID_MATERIAL: "Freight needs a material.",
  OVER_PAYLOAD: `Freight can weigh at most ${MAX_LOAD_KG} kg.`,
  UNKNOWN_ID: "That car, mission, alert or scenario does not exist.",
  INVALID_STATE: "Not possible in the current state (is the simulation stopped?).",
  NOT_BOARDED: "that passenger is not on board",
};

function toast(text, ok) {
  const el = $("#toast");
  el.textContent = text;
  el.dataset.ok = String(ok);
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, TOAST_MS);
}

// --- talking to the API -------------------------------------------------------------

// ``asRole`` overrides the top-bar role, e.g. buttons inside a car are pressed by a passenger
// (Huy's choice, 14/11). The API and the modules still check every role.
async function send(path, body = {}, doneText = "Sent", asRole = null) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: asRole || role(), ...body }),
    });
  } catch {
    toast("The server is not reachable. Is python -m app still running?", false);
    return null;
  }
  const result = await response.json().catch(() => ({ ok: false }));
  if (result.ok) {
    lastSendAt = Date.now();
    toast(doneText, true);
  } else {
    toast(REASON_TEXT[result.reason] || result.detail || `Refused (${response.status}).`, false);
  }
  if (!liveSocket) refresh(); // polling mode: show the effect right away
  if (!result.ok) refreshAudit();
  return result;
}

// F9: "accepted by the API" is not "done". For a few seconds after an action, refusals
// that appear in the event log (a cabin rejecting a destination, a refused hall call or
// mission) are shown as a toast, so the page never claims an outcome that did not happen.
const REFUSAL_WINDOW_MS = 4000;
const REFUSAL = /event=\w*REJECTED|accepted=False|status=REJECTED/;
let lastSendAt = 0;
let lastEventLine = null;

function showNewRefusals(snap) {
  const lines = snap.last_events;
  const from = lastEventLine === null ? lines.length : lines.lastIndexOf(lastEventLine) + 1;
  lastEventLine = lines.length ? lines[lines.length - 1] : null;
  if (Date.now() - lastSendAt > REFUSAL_WINDOW_MS) return;
  const refused = lines.slice(from).filter((line) => REFUSAL.test(line));
  if (refused.length) {
    const fields = Object.fromEntries([...refused[refused.length - 1].matchAll(/(\w+)=(\S+)/g)].map((m) => [m[1], m[2]]));
    const what = [fields.car_id, fields.event || fields.status, fields.floor && `floor ${fields.floor}`]
      .filter(Boolean).join(" ");
    toast(`Refused: ${what}${fields.reason ? ` (${REASON_TEXT[fields.reason] || fields.reason})` : ""}`, false);
  }
}

let liveSocket = null;
let pollTimer = null;

function setLink(mode, text) {
  const el = $("#link");
  el.dataset.mode = mode;
  el.textContent = text;
}

function connect() {
  let opened = false;
  let ws;
  try {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  } catch {
    startPolling();
    return;
  }
  ws.onopen = () => {
    opened = true;
    liveSocket = ws;
    stopPolling();
    setLink("live", "Live");
  };
  ws.onmessage = (event) => render(JSON.parse(event.data));
  ws.onclose = () => {
    liveSocket = null;
    if (opened) setTimeout(connect, 1000); // the server restarted: try again
    else startPolling();                  // never opened: no WebSocket support
  };
}

function startPolling() {
  if (pollTimer) return;
  setLink("poll", `Refreshing every ${POLL_MS / 1000} s`);
  pollTimer = setInterval(refresh, POLL_MS);
  refresh();
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

let refreshing = false; // never two polls in flight: their answers could arrive out of order

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const response = await fetch("/snapshot");
    render(await response.json());
    if (!liveSocket) setLink("poll", `Refreshing every ${POLL_MS / 1000} s`);
  } catch {
    setLink("down", "Server not reachable");
  } finally {
    refreshing = false;
  }
}

// --- UI-01 fleet view ---------------------------------------------------------------

let fleet = []; // car ids of the running scenario, in snapshot order

function buildHoistway(cars) {
  const hw = $("#hoistway");
  hw.replaceChildren();
  hw.style.setProperty("--floors", FLOORS);
  hw.style.setProperty("--cars", cars.length);
  const scale = document.createElement("div");
  scale.className = "floor-scale";
  for (let f = FLOORS; f >= 1; f -= 1) {
    if (f === 1 || f % 5 === 0) {
      const s = document.createElement("span");
      s.textContent = f;
      s.style.top = `calc(${FLOORS - f} * var(--floor-h))`;
      scale.append(s);
    }
  }
  hw.append(scale);
  for (const car of cars) {
    const shaft = document.createElement("div");
    shaft.className = "shaft";
    shaft.dataset.car = car;
    shaft.innerHTML = `<span class="shaft-label">${esc(car)}</span><div class="stops"></div>
      <div class="car" data-car="${esc(car)}"><span class="readout">-</span></div>`;
    hw.append(shaft);
  }
  const lanterns = document.createElement("div");
  lanterns.className = "lanterns";
  lanterns.id = "lanterns";
  lanterns.setAttribute("aria-label", "Hall calls");
  hw.append(lanterns);
}

function renderFleet(snap) {
  for (const car of snap.cars) {
    const shaft = $(`.shaft[data-car="${car.car_id}"]`);
    if (!shaft) continue;
    const el = $(".car", shaft);
    // position is continuous (floor 1 = 1.0); top edge of floor f is (FLOORS - f) floors down
    el.style.top = `calc(${fromTop(car.position) + 0.5} * var(--floor-h))`;
    el.dataset.door = car.door;
    el.dataset.mode = car.mode;
    el.dataset.stale = String(car.telemetry_age_s !== null && car.telemetry_age_s > TELEMETRY_STALE_S);
    $(".readout", el).textContent = `${car.floor_display}${car.direction === "IDLE" ? "" : ARROW[car.direction]}`;
    el.title = `${car.car_id}: floor ${car.floor_display}, door ${car.door}, ${car.mode}`;
    $(".stops", shaft).innerHTML = car.stops.map((s) => (
      `<span class="stop-mark" data-kind="${esc(s.kind)}" title="${esc(s.kind)} ${s.floor}"
        style="top: calc(${FLOORS - s.floor} * var(--floor-h))"></span>`
    )).join("");
  }

  const byFloor = new Map();
  for (const call of snap.calls) {
    if (call.status === "SERVED") continue;
    if (!byFloor.has(call.floor)) byFloor.set(call.floor, []);
    byFloor.get(call.floor).push(call);
  }
  $("#lanterns").innerHTML = [...byFloor.entries()].map(([floor, calls]) => (
    `<div class="lantern" style="top: calc(${FLOORS - floor} * var(--floor-h))">${calls.map((c) => {
      // UI-02: accepted-but-unassigned (outline) vs owned by a car (filled)
      const owned = c.status === "ASSIGNED" || c.status === "RESERVED";
      return `<b class="${owned ? "assigned" : "pending"}" title="${esc(c.request_id)} ${esc(c.status)}">${ARROW[c.direction]}${owned && c.owner ? esc(c.owner) : ""}</b>`;
    }).join("")}</div>`
  )).join("");

  $("#car-rows").innerHTML = snap.cars.map((car) => {
    const loadClass = car.load_kg >= FULL_LOAD_KG ? "warn" : "";
    const loadNote = car.load_kg > MAX_LOAD_KG ? " overload" : car.load_kg >= FULL_LOAD_KG ? " full" : "";
    const faults = car.faults.length ? `<div class="fault-list">${car.faults.map(esc).join(", ")}</div>` : "";
    const stale = car.telemetry_age_s === null ? '<div class="quiet">no telemetry yet</div>' : "";
    return `<tr>
      <td>${esc(car.car_id)}</td>
      <td>${car.floor_display}<div class="quiet">${fmt(car.position, 2)}</div></td>
      <td>${ARROW[car.direction]}</td>
      <td>${esc(car.door.toLowerCase())}${car.locked ? "" : ' <span class="warn">unlocked</span>'}</td>
      <td class="${loadClass}">${fmt(car.load_kg, 0)} kg${loadNote}<div class="quiet">${car.passengers} aboard</div></td>
      <td>${esc(car.mode)}${faults}${stale}</td>
      <td>v${car.route_version}<div class="quiet">${car.stops.map((s) => s.floor).join(" → ") || "no stops"}</div></td>
      <td>${esc(car.mission_id || "-")}</td>
    </tr>`;
  }).join("");
}

// --- UI-04 Control Center -----------------------------------------------------------

function renderAlerts(snap) {
  const alerts = [...snap.alerts].reverse(); // newest first
  $("#alerts").innerHTML = alerts.length ? alerts.map((a) => `
    <li data-ack="${a.acknowledged}">
      <span class="kind">${esc(a.kind)}${a.car_id ? ` in ${esc(a.car_id)}` : ""}</span>
      <span>${esc(a.details)} <span class="quiet">at ${fmt(a.t_sim)} s</span></span>
      <span class="row-actions">${a.acknowledged ? "Acknowledged"
        : `<button type="button" data-ack="${esc(a.alert_id)}">Acknowledge</button>`}</span>
    </li>`).join("")
    : '<li class="empty">No alerts.</li>';
}

function renderMissions(snap) {
  // The table is redrawn on every snapshot: keep what the user typed in the weight fields.
  const typed = Object.fromEntries($$("[data-weight-for]").map((i) => [i.dataset.weightFor, i.value]));
  const focused = document.activeElement && document.activeElement.dataset
    ? document.activeElement.dataset.weightFor : undefined;
  $("#missions").innerHTML = snap.missions.length ? snap.missions.map((m) => {
    const excluded = m.candidates.filter((c) => c.excluded)
      .map((c) => `${esc(c.car_id)}: ${esc(c.excluded)}`).join(", ");
    const why = [m.reason, excluded && `excluded ${excluded}`].filter(Boolean).join("; ");
    const live = m.car_id && !["COMPLETED", "FAILED", "REJECTED", "UNAVAILABLE"].includes(m.status);
    const weight = m.mission_type === "FREIGHT"
      ? `<input class="load-kg" type="number" min="1" step="1" placeholder="kg" aria-label="Loaded weight for ${esc(m.mission_id)}" data-weight-for="${esc(m.mission_id)}">`
      : "";
    const actions = live ? `${weight}
      <button type="button" data-confirm="LOADED" data-mission="${esc(m.mission_id)}" data-car="${esc(m.car_id)}">Loaded</button>
      <button type="button" data-confirm="UNLOADED" data-mission="${esc(m.mission_id)}" data-car="${esc(m.car_id)}">Unloaded</button>
      <button type="button" data-confirm="LOAD_FAILED" data-mission="${esc(m.mission_id)}" data-car="${esc(m.car_id)}">Load failed</button>` : "";
    return `<tr>
      <td>${esc(m.mission_id)}</td>
      <td>${esc(m.mission_type.toLowerCase())}</td>
      <td>${m.origin ?? "-"} → ${m.destination ?? "-"}</td>
      <td><span class="status" data-status="${esc(m.status)}">${esc(m.status)}</span></td>
      <td>${esc(m.car_id || "-")}</td>
      <td class="excluded">${esc(why) || ""}</td>
      <td class="row-actions">${actions}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="7" class="quiet">No missions yet. Request one below.</td></tr>';
  $$("[data-weight-for]").forEach((input) => {
    const id = input.dataset.weightFor;
    if (typed[id]) input.value = typed[id];
    if (id === focused) input.focus();
  });
}

// --- SRS 5.3 refused requests (GET /audit) -------------------------------------------

async function refreshAudit() {
  try {
    const entries = await (await fetch("/audit")).json();
    const rows = entries.slice(-AUDIT_SHOWN).reverse();
    $("#refusals").innerHTML = rows.length ? rows.map((e) => `<tr>
        <td>${fmt(e.t_sim)} s</td><td>${esc((e.role || "none").toLowerCase().replace("_", " "))}</td>
        <td>${esc(e.command)}</td><td>${esc(REASON_TEXT[e.reason] || e.reason || `status ${e.status}`)}</td>
      </tr>`).join("") : '<tr><td colspan="4" class="quiet">Nothing refused yet.</td></tr>';
  } catch { /* the snapshot loop reports a lost server */ }
}

// --- UI-05 simulation console -------------------------------------------------------

// Wear NFR (DECISIONS D-71..D-79): floors travelled by P1-P4 since the last 6-monthly reset,
// and the month's spread (max - min) / max against the limit. G1 is not compared.
function renderWear(wear) {
  if (!wear) return;
  const counters = Object.entries(wear.counters);
  const top = Math.max(1, ...counters.map(([, v]) => v));
  const pct = (v) => (v == null ? "-" : `${(v * 100).toFixed(1)} %`);
  const over = wear.spread != null && wear.spread > wear.limit;
  $("#wear-rule").textContent = `month ${wear.month}: spread ${pct(wear.spread)} (limit ${pct(wear.limit)})`;
  $("#wear-rule").dataset.state = over ? "over" : "ok";
  $("#wear").innerHTML = counters.map(([car, v]) => {
    const out = wear.excluded.includes(car) ? " (maintenance, not compared)" : "";
    return `<div class="wear-row"><span>${esc(car)}</span>
      <span class="wear-bar"><i style="width:${(v / top) * 100}%"></i></span>
      <span>${fmt(v, 0)} floors${out}</span></div>`;
  }).join("") || '<p class="quiet">No telemetry yet.</p>';
  const closed = wear.months.slice(-3).map((m) => `month ${m.month}: ${pct(m.spread)} ${m.ok ? "pass" : "FAIL"}`);
  $("#wear-months").textContent = closed.length ? `Closed: ${closed.join(" · ")}` : "No month closed yet (a month is 30 simulated days).";
}

function renderConsole(snap) {
  const m = snap.metrics;
  const tiles = [
    ["Mean wait", seconds(m.mean_wait)],
    ["Median wait", seconds(m.median_wait)],
    ["95th percentile", seconds(m.p95_wait)],
    ["Longest wait", seconds(m.max_wait)],
    ["Not yet served", m.unserved],
    ["Delay violations", m.delay_violations],
    ["Moved after a fault", m.fault_reassigned ?? 0],
  ];
  $("#metrics").innerHTML = tiles.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
  renderWear(snap.wear);

  const calls = [...snap.calls].reverse();
  $("#calls").innerHTML = calls.length ? calls.map((c) => `<tr>
      <td>${esc(c.request_id)}</td><td>${c.floor}</td><td>${ARROW[c.direction]}</td>
      <td><span class="status" data-status="${esc(c.status)}">${esc(c.status)}</span></td>
      <td>${esc(c.owner || "-")}</td><td>${fmt(c.created_at)} s</td>
    </tr>`).join("") : '<tr><td colspan="6" class="quiet">No hall calls.</td></tr>';

  const events = snap.last_events.slice(-EVENTS_SHOWN);
  const log = $("#events");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
  log.innerHTML = events.map((line) => `<li>${esc(line)}</li>`).join("");
  if (atBottom) log.scrollTop = log.scrollHeight;
}


// --- UI-02 hall panel ---------------------------------------------------------------

function buildHallPanel() {
  const grid = $("#hall-panel");
  for (let f = FLOORS; f >= 1; f -= 1) {
    const landing = document.createElement("div");
    landing.className = "landing";
    // No UP at the top floor and no DOWN at the ground floor, like a real landing (REQ-02).
    landing.innerHTML = `<span class="num">${f}</span><span class="btns">
      <button type="button" class="call-btn" data-call="UP" data-floor="${f}" aria-label="Floor ${f} up" ${f === FLOORS ? "hidden" : ""}>▲</button>
      <button type="button" class="call-btn" data-call="DOWN" data-floor="${f}" aria-label="Floor ${f} down" ${f === 1 ? "hidden" : ""}>▼</button>
    </span>`;
    grid.append(landing);
  }
}

function renderHall(snap) {
  const lit = new Map();
  for (const c of snap.calls) {
    if (c.status === "SERVED") continue;
    const owned = c.status === "ASSIGNED" || c.status === "RESERVED";
    lit.set(`${c.floor}:${c.direction}`, owned ? "owned" : "pending");
  }
  $$(".call-btn").forEach((b) => {
    const state = lit.get(`${b.dataset.floor}:${b.dataset.call}`);
    if (state) b.dataset.lit = state; else delete b.dataset.lit;
  });
}

// --- UI-03 cabin panel --------------------------------------------------------------

let cabinCar = null;

function buildCabinPicker(cars) {
  if (!cars.includes(cabinCar)) cabinCar = cars[0] ?? null;
  $("#cabin-cars").innerHTML = cars.map((c) => (
    `<button type="button" role="radio" aria-checked="${c === cabinCar}" data-cabin-car="${esc(c)}">${esc(c)}</button>`
  )).join("");
}

function buildCabinPanel() {
  const pad = $("#cabin-buttons");
  pad.innerHTML = Array.from({ length: FLOORS }, (_, i) => i + 1).map((f) => (
    `<button type="button" class="dest-btn" data-dest="${f}" aria-label="Go to floor ${f}">${f}</button>`
  )).join("");
}

function selectCabinCar(car) {
  cabinCar = car;
  $$("#cabin-cars button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.cabinCar === car)));
  if (lastSnapshot) renderCabin(lastSnapshot);
}


// UI-03: destination states come from the Cabin's own record, ``snap.dests`` (DestView:
// car, floor, passenger, PENDING or SERVED). The Cabin keeps a SERVED entry until that
// passenger presses again, so the page shows "served" for SERVED_SHOWN_S simulated seconds
// after it first sees it, then lets the button go dark.
const servedSince = new Map(); // "car:floor:passenger" -> t_sim first seen SERVED

function trackDestinations(snap, isReset) {
  if (isReset) servedSince.clear();
  const current = new Set();
  for (const d of snap.dests) {
    if (d.status !== "SERVED") continue;
    const key = `${d.car_id}:${d.floor}:${d.passenger_id}`;
    current.add(key);
    if (!servedSince.has(key)) servedSince.set(key, snap.t_sim);
  }
  for (const key of servedSince.keys()) if (!current.has(key)) servedSince.delete(key);
}

function destinationState(snap, carId, floor) {
  const here = snap.dests.filter((d) => d.car_id === carId && d.floor === floor);
  if (here.some((d) => d.status === "PENDING")) return "pending";
  const served = here.some((d) => {
    const since = servedSince.get(`${d.car_id}:${d.floor}:${d.passenger_id}`);
    return d.status === "SERVED" && since !== undefined && snap.t_sim - since <= SERVED_SHOWN_S;
  });
  return served ? "served" : "";
}

function renderCabin(snap) {
  const car = snap.cars.find((c) => c.car_id === cabinCar);
  if (!car) { // no car in this scenario: show nothing rather than a previous car's state
    $("#cabin-floor").textContent = "-";
    $("#cabin-dir").textContent = "";
    $("#cabin-status").textContent = "No car selected";
    return;
  }
  $("#cabin-floor").textContent = car.floor_display;
  $("#cabin-dir").textContent = car.direction === "IDLE" ? "" : ARROW[car.direction];
  const over = car.load_kg > MAX_LOAD_KG ? ", overload" : car.load_kg >= FULL_LOAD_KG ? ", full" : "";
  $("#cabin-status").textContent =
    `door ${car.door.toLowerCase()}, ${car.passengers} aboard, ${fmt(car.load_kg, 0)} kg${over}, ${car.mode}`;
  $$(".dest-btn").forEach((b) => {
    const floor = Number(b.dataset.dest);
    const state = destinationState(snap, car.car_id, floor);
    if (state) b.dataset.state = state; else delete b.dataset.state;
    b.setAttribute("aria-label", `Go to floor ${floor}${state ? ` (${state})` : ""}`);
  });
}

function passengerId() {
  return $("#cabin-pax").value.trim() || "PAX-1";
}

// --- OPT-06 optimization view -------------------------------------------------------

function renderPolicy(snap) {
  const p = snap.policy;
  const tiles = [
    ["Version", p.version],
    ["Group span R", `${p.R} floors`],
    ["Time window W", `${p.W} s`],
  ];
  $("#policy").innerHTML = tiles.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
}

// OPT-06, D-68: runs started from the UI, their recommendation and the supervisor's decision.
function renderOptimization(snap) {
  const runs = (snap.optimization && snap.optimization.runs) || [];
  if (!runs.length) {
    $("#opt-runs").innerHTML = `<tr><td colspan="9" class="hint">No run yet.</td></tr>`;
    return;
  }
  const num = (v) => (v === null || v === undefined ? "-" : Number(v).toFixed(1));
  $("#opt-runs").innerHTML = runs.slice().reverse().map((r) => {
    const status = r.status === "RUNNING" ? `running ${Math.round(r.progress * 100)}%` : r.status.toLowerCase();
    const best = r.best ? `(${r.best.R}, ${r.best.W}) as ${esc(r.best.version)}` : "-";
    const canDecide = r.status === "DONE" && r.decision === "PENDING";
    const buttons = canDecide
      ? `<button type="button" data-run-decide="${esc(r.run_id)}" data-approve="1">Approve</button>
         <button type="button" data-run-decide="${esc(r.run_id)}" data-approve="0">Reject</button>`
      : "";
    const detail = r.detail ? ` title="${esc(r.detail)}"` : "";
    return `<tr${detail}><td>${esc(r.run_id)}</td><td>${esc(r.method)}${r.quick ? " (quick)" : ""}</td>
      <td>${esc(status)}</td><td>${r.evaluated}</td><td>${best}</td><td>${num(r.objective)}</td>
      <td>${num(r.baseline_objective)}</td><td>${esc(r.decision.toLowerCase())}</td><td>${buttons}</td></tr>`;
  }).join("");
}

// --- render everything --------------------------------------------------------------

function render(snap) {
  // Time going backwards means reset or replay. (Out-of-order poll answers cannot cause
  // it: refresh() never has two requests in flight.)
  const isReset = lastSnapshot !== null && snap.t_sim < lastSnapshot.t_sim;
  lastSnapshot = snap;
  syncFleet(snap);
  showNewRefusals(snap);
  trackDestinations(snap, isReset);
  const state = $("#sim-state");
  state.textContent = snap.sim_state.toLowerCase();
  state.dataset.state = snap.sim_state;
  $("#sim-time").textContent = fmt(snap.t_sim);
  $("#sim-seed").textContent = snap.seed;
  $("#sim-policy").textContent = `${snap.policy.version} (R=${snap.policy.R}, W=${snap.policy.W})`;
  renderFleet(snap);
  renderAlerts(snap);
  renderMissions(snap);
  renderConsole(snap);
  renderHall(snap);
  renderCabin(snap);
  renderPolicy(snap);
  renderOptimization(snap);
}

// --- wiring ---------------------------------------------------------------------------

function wireTabs() {
  $$(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tabs button").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    $$(".panel").forEach((p) => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  }));
}

function fillCarSelects(cars) {
  $$(".car-select").forEach((sel) => {
    const keep = sel.value;
    sel.innerHTML = cars.map((c) => `<option>${esc(c)}</option>`).join("");
    if (cars.includes(keep)) sel.value = keep;
  });
}

// UI-01: when the scenario's fleet changes (reset to AT-04, AT-05, ...), rebuild every
// place that lists cars, so no shaft or picker shows a car that is not in the simulation.
function syncFleet(snap) {
  const ids = snap.cars.map((c) => c.car_id);
  if (ids.join() === fleet.join()) return;
  fleet = ids;
  buildHoistway(ids);
  buildCabinPicker(ids);
  fillCarSelects(ids);
}

async function fillScenarios() {
  try {
    const names = await (await fetch("/scenarios")).json();
    $("#scenario").innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join("");
  } catch { /* the server will be retried by the snapshot loop */ }
}

function number(value) {
  return value === "" ? null : Number(value);
}

function wireForms() {
  const missionForm = $("#mission-form");
  const syncFreight = () => {
    const freight = missionForm.mission_type.value === "FREIGHT";
    $$(".freight-only", missionForm).forEach((el) => { el.hidden = !freight; });
  };
  missionForm.mission_type.addEventListener("change", syncFreight);
  syncFreight();
  missionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const f = missionForm;
    const body = {
      mission_type: f.mission_type.value,
      origin: number(f.origin.value),
      destination: number(f.destination.value),
    };
    if (body.mission_type === "FREIGHT") {
      body.material = f.material.value;
      body.weight_kg = number(f.weight_kg.value);
    }
    send("/mission", body, "Mission request sent. Its status appears in the list above.");
  });

  $("#recovery-form").addEventListener("submit", (event) => {
    event.preventDefault();
    send("/recovery", { car_id: event.target.car_id.value }, "Recovery request sent. The car moves only after its own checks pass.");
  });

  $("#fault-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const f = event.target;
    const clear = event.submitter && event.submitter.value === "clear";
    send(clear ? "/fault/clear" : "/fault", { car_id: f.car_id.value, kind: f.kind.value },
      clear ? "Fault cleared." : "Fault injected.");
  });

  $("#board-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const leave = event.submitter && event.submitter.value === "exit";
    const body = { car_id: cabinCar, passenger_id: passengerId() };
    if (!leave) body.weight_kg = number(event.target.weight_kg.value);
    send(leave ? "/passenger/exit" : "/passenger/board", body,
      leave ? `${body.passenger_id} left ${cabinCar}.` : `${body.passenger_id} boarded ${cabinCar}.`);
  });

  $("#policy-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const approve = !(event.submitter && event.submitter.value === "reject");
    send("/policy/approve", { run_id: event.target.run_id.value.trim(), approve },
      approve ? "Policy approval sent. Nothing changes if the run number is unknown." : "Policy rejection sent.");
  });

  document.addEventListener("click", (event) => {
    const t = event.target.closest("button");
    if (!t) return;
    if (t.dataset.call) {
      send("/hall-call", { floor: Number(t.dataset.floor), direction: t.dataset.call },
        `Call sent: floor ${t.dataset.floor} ${t.dataset.call.toLowerCase()}.`);
    }
    if (t.dataset.cabinCar) selectCabinCar(t.dataset.cabinCar);
    if (t.dataset.dest) {
      send("/destination", { car_id: cabinCar, floor: Number(t.dataset.dest), passenger_id: passengerId() },
        `Floor ${t.dataset.dest} sent to ${cabinCar}.`, "PASSENGER");
    }
    if (t.dataset.door) {
      send("/door", { car_id: cabinCar, open: t.dataset.door === "true" },
        t.dataset.door === "true" ? "Door open pressed." : "Door close pressed.", "PASSENGER");
    }
    if (t.hasAttribute("data-alarm")) send("/alarm", { car_id: cabinCar }, `Alarm pressed in ${cabinCar}.`, "PASSENGER");
    if (t.hasAttribute("data-optimize")) send("/optimization/run", { method: $("#opt-method").value, quick: true }, "Optimization started.");
    if (t.dataset.runDecide) {
      const approve = t.dataset.approve === "1";
      send("/policy/approve", { run_id: t.dataset.runDecide, approve },
        approve ? `Approval sent for ${t.dataset.runDecide}.` : `Rejection sent for ${t.dataset.runDecide}.`);
    }
    if (t.dataset.ack) send("/alert/ack", { alert_id: t.dataset.ack }, "Acknowledgement sent.");
    if (t.dataset.confirm) {
      const body = { mission_id: t.dataset.mission, car_id: t.dataset.car, action: t.dataset.confirm };
      const kg = $(`[data-weight-for="${CSS.escape(t.dataset.mission)}"]`);
      if (t.dataset.confirm === "LOADED" && kg) { // F10: the Cabin sets the cargo load to this
        if (!kg.value) { toast("Enter the loaded weight in kg first.", false); kg.focus(); return; }
        body.weight_kg = Number(kg.value);
      }
      send("/mission/confirm", body, `${t.dataset.confirm.toLowerCase().replace("_", " ")} sent.`);
    }
    if (t.dataset.fire) send("/fire", { active: t.dataset.fire === "true" }, "Fire alarm updated.");
    if (t.dataset.maint) {
      send("/maintenance", { car_id: $("#maint-car").value, on: t.dataset.maint === "true" }, "Maintenance updated.");
    }
    if (t.dataset.sim === "start") send("/sim/start", {}, "Simulation started.");
    if (t.dataset.sim === "pause") send("/sim/pause", {}, "Simulation paused.");
    if (t.dataset.sim === "replay") {
      const trace = $("#trace").value.trim();
      if (!trace) { toast("Enter the path of a recorded trace first.", false); return; }
      send("/sim/replay", { trace }, "Replay finished.").then((r) => {
        if (r && r.ok) toast(`Replay finished: ${r.id === "IDENTICAL" ? "identical to the recorded run" : "DIFFERENT from the recorded run"}.`, r.id === "IDENTICAL");
      });
    }
    if (t.dataset.sim === "reset") {
      send("/sim/reset", { scenario: $("#scenario").value, seed: number($("#seed").value) ?? 0 },
        "Simulation reset. Press Start to run it.");
    }
  });

  $("#speed").addEventListener("change", (event) => {
    send("/sim/speed", { factor: Number(event.target.value) }, `Speed set to ${event.target.value}×.`);
  });
}

buildHallPanel();
buildCabinPanel();
wireTabs();
wireForms();
fillScenarios();
connect();
refreshAudit();
setInterval(refreshAudit, AUDIT_MS);
