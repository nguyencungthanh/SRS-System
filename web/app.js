// Smart Elevator System UI (Huy, HY-06). Plain JavaScript, no framework (PROJECT_PLAN.md).
//
// Reads the Snapshot (contracts/ui.py) from the WebSocket /ws. If the WebSocket is not
// available (An's requirements.txt has no WebSocket library yet, see ai-log/huy.md A1),
// it falls back to GET /snapshot every POLL_MS. Commands go to the API with the role
// chosen in the top bar; the API and the Control Center check the role, not this page.

"use strict";

// Copied from contracts/config.py (the browser cannot import Python). Keep in sync.
const FLOORS = 40;
const CARS = ["P1", "P2", "P3", "P4", "G1"];
const FULL_LOAD_KG = 900;
const MAX_LOAD_KG = 1000;

const POLL_MS = 500;            // fallback refresh when /ws is not available
const STALE_TELEMETRY_S = 2.0;  // dim a car whose telemetry is older than this
const TOAST_MS = 3500;
const EVENTS_SHOWN = 50;

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

async function send(path, body = {}, doneText = "Sent") {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: role(), ...body }),
    });
  } catch {
    toast("The server is not reachable. Is python -m app still running?", false);
    return null;
  }
  const result = await response.json().catch(() => ({ ok: false }));
  if (result.ok) {
    toast(doneText, true);
  } else {
    toast(REASON_TEXT[result.reason] || result.detail || `Refused (${response.status}).`, false);
  }
  if (!liveSocket) refresh(); // polling mode: show the effect right away
  return result;
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

async function refresh() {
  try {
    const response = await fetch("/snapshot");
    render(await response.json());
    if (!liveSocket) setLink("poll", `Refreshing every ${POLL_MS / 1000} s`);
  } catch {
    setLink("down", "Server not reachable");
  }
}

// --- UI-01 fleet view ---------------------------------------------------------------

function buildHoistway() {
  const hw = $("#hoistway");
  hw.style.setProperty("--floors", FLOORS);
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
  for (const car of CARS) {
    const shaft = document.createElement("div");
    shaft.className = "shaft";
    shaft.dataset.car = car;
    shaft.innerHTML = `<span class="shaft-label">${car}</span><div class="stops"></div>
      <div class="car" data-car="${car}"><span class="readout">-</span></div>`;
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
    el.dataset.stale = String(car.telemetry_age_s !== null && car.telemetry_age_s > STALE_TELEMETRY_S);
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
  $("#missions").innerHTML = snap.missions.length ? snap.missions.map((m) => {
    const excluded = m.candidates.filter((c) => c.excluded)
      .map((c) => `${esc(c.car_id)}: ${esc(c.excluded)}`).join(", ");
    const why = [m.reason, excluded && `excluded ${excluded}`].filter(Boolean).join("; ");
    const live = m.car_id && !["COMPLETED", "FAILED", "REJECTED", "UNAVAILABLE"].includes(m.status);
    const actions = live ? `
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
}

// --- UI-05 simulation console -------------------------------------------------------

function renderConsole(snap) {
  const m = snap.metrics;
  const tiles = [
    ["Mean wait", seconds(m.mean_wait)],
    ["Median wait", seconds(m.median_wait)],
    ["95th percentile", seconds(m.p95_wait)],
    ["Longest wait", seconds(m.max_wait)],
    ["Not yet served", m.unserved],
    ["Delay violations", m.delay_violations],
  ];
  $("#metrics").innerHTML = tiles.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");

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

// --- render everything --------------------------------------------------------------

function render(snap) {
  lastSnapshot = snap;
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
}

// --- wiring ---------------------------------------------------------------------------

function wireTabs() {
  $$(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tabs button").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    $$(".panel").forEach((p) => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  }));
}

function fillCarSelects() {
  $$(".car-select").forEach((sel) => {
    sel.innerHTML = CARS.map((c) => `<option>${c}</option>`).join("");
  });
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
    send("/mission", body, "Mission requested. Its status appears in the list above.");
  });

  $("#recovery-form").addEventListener("submit", (event) => {
    event.preventDefault();
    send("/recovery", { car_id: event.target.car_id.value }, "Recovery request sent.");
  });

  $("#fault-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const f = event.target;
    const clear = event.submitter && event.submitter.value === "clear";
    send(clear ? "/fault/clear" : "/fault", { car_id: f.car_id.value, kind: f.kind.value },
      clear ? "Fault cleared." : "Fault injected.");
  });

  document.addEventListener("click", (event) => {
    const t = event.target.closest("button");
    if (!t) return;
    if (t.dataset.ack) send("/alert/ack", { alert_id: t.dataset.ack }, "Alert acknowledged.");
    if (t.dataset.confirm) {
      send("/mission/confirm", { mission_id: t.dataset.mission, car_id: t.dataset.car, action: t.dataset.confirm },
        `Marked ${t.dataset.confirm.toLowerCase().replace("_", " ")}.`);
    }
    if (t.dataset.fire) send("/fire", { active: t.dataset.fire === "true" }, "Fire alarm updated.");
    if (t.dataset.maint) {
      send("/maintenance", { car_id: $("#maint-car").value, on: t.dataset.maint === "true" }, "Maintenance updated.");
    }
    if (t.dataset.sim === "start") send("/sim/start", {}, "Simulation started.");
    if (t.dataset.sim === "pause") send("/sim/pause", {}, "Simulation paused.");
    if (t.dataset.sim === "reset") {
      send("/sim/reset", { scenario: $("#scenario").value, seed: number($("#seed").value) ?? 0 },
        "Simulation reset. Press Start to run it.");
    }
  });

  $("#speed").addEventListener("change", (event) => {
    send("/sim/speed", { factor: Number(event.target.value) }, `Speed set to ${event.target.value}×.`);
  });
}

buildHoistway();
fillCarSelects();
wireTabs();
wireForms();
fillScenarios();
connect();
