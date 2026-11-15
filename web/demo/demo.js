// How the code works: plays back web/demo/stories.js (python -m app.demo).
// Left: the four modules as lanes; for every message, what each one received or sent,
// the functions that ran (recorded by a profiler) and what they decided. Right: the
// building at the same simulated time.
"use strict";

const DATA = window.DEMO;
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const fmt = (v, d = 1) => (v == null ? "-" : Number(v).toFixed(d).replace(/\.0+$/, ""));

const LANES = [
  { key: "world", name: "World", role: "simulated people, buttons, sensors and staff" },
  { key: "dispatch", name: "Dispatch", role: "decides which car serves each call. It never moves a car." },
  { key: "cabin", name: "Cabin", role: "one per car. It moves only when its safety checks pass." },
  { key: "cc", name: "Control Center", role: "operators: approves missions, shows alerts" },
];
const MODES = {
  AUTO: "#0e8f86", FULL: "#c77700", OVERLOAD_HOLD: "#c93030", FAULT_HOLD: "#6e1b1b",
  MAINTENANCE: "#8a94a3", MISSION_DRAINING: "#d6336c", MISSION_EXCLUSIVE: "#d6336c", FIRE: "#e8590c",
};
const MODE_LABEL = {
  AUTO: "normal", FULL: "full", OVERLOAD_HOLD: "overload", FAULT_HOLD: "fault",
  MAINTENANCE: "maintenance", MISSION_EXCLUSIVE: "mission",
};
const DOOR_OPEN = { CLOSED: 0, OPENING: 0.5, OPEN: 1, CLOSING: 0.5 };
const DOOR_STATES = ["CLOSED", "OPENING", "OPEN", "CLOSING"];
const KEY_FUNCTIONS = new Set([
  "ordinary_exclusion", "hall_route", "route_etas", "mission_candidates", "validate",
  "next_state", "Cabin._can_move", "Cabin._must_hold_open", "validate_request", "may_request",
]);

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const laneColor = (lane) => css(`--${lane}`);

// --- state -------------------------------------------------------------------------------

let story = null;
let events = [];
let visible = []; // indices into events
let byLane = {}; // lane -> sorted visible-or-not event indices involving it
let cursor = -1; // index into visible
let t = 0;
let playing = false;
let holdUntil = 0;
let lastFrame = 0;
let speed = 1;

// --- story selection -------------------------------------------------------------------

function buildStoryTabs() {
  const labels = ["1", "2", "3", "4", "A", "B"];
  $("#stories").innerHTML = DATA.stories.map((s, i) => (
    `<button type="button" data-story="${i}"><b>${labels[i] ?? i + 1}</b>${esc(s.title)}</button>`
  )).join("");
  $("#stories").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-story]");
    if (b) selectStory(Number(b.dataset.story));
  });
}

function selectStory(i) {
  pause();
  story = DATA.stories[i];
  events = story.events;
  try { history.replaceState(null, "", `#${story.key}`); } catch { /* file:// */ }
  $$("#stories button").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.story) === i)));
  $("#tagline").textContent = story.tagline;
  $("#reqs").innerHTML = story.requirements.map((r) => `<span>${esc(r)}</span>`).join("");
  $("#note").hidden = !story.note;
  $("#note").textContent = story.note;
  $("#scrub").max = story.until;
  byLane = {};
  for (const lane of LANES) byLane[lane.key] = [];
  events.forEach((e, i) => {
    for (const lane of LANES) if (roles(e, lane.key).length) byLane[lane.key].push(i);
  });
  computeVisible();
  buildBuilding();
  cursor = -1;
  t = 0;
  renderEvent(false);
  renderBuilding();
}

function $$(s) { return Array.from(document.querySelectorAll(s)); }

function computeVisible() {
  const reports = $("#reports").checked;
  const current = cursor >= 0 ? visible[cursor] : -1;
  visible = [];
  events.forEach((e, i) => { if (reports || !e.minor) visible.push(i); });
  cursor = current < 0 ? -1 : lastVisibleAtOrBefore(current);
  buildTicks();
}

function lastVisibleAtOrBefore(eventIndex) {
  let lo = 0, hi = visible.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (visible[mid] <= eventIndex) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

function lastVisibleAtTime(time) {
  let lo = 0, hi = visible.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (events[visible[mid]].t <= time + 1e-9) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

// --- who did what for one message ----------------------------------------------------------

function roles(e, lane) {
  const out = [];
  if (e.fromLane === lane && e.type !== "Timer") {
    out.push({ verb: "SENDS", h: e.madeBy == null ? null : story.handlings[e.madeBy] });
  }
  for (const i of e.handledBy) {
    const h = story.handlings[i];
    if (h.lane === lane) out.push({ verb: e.type === "Timer" ? "TIMER" : "RECEIVES", h });
  }
  return out;
}

// --- lanes -------------------------------------------------------------------------------------

function buildLanes() {
  $("#lanes").innerHTML = LANES.map((l) => `
    <article class="lane idle" data-lane="${l.key}">
      <header class="lane-head">
        <span class="name">${esc(l.name)}</span><span class="role">${esc(l.role)}</span>
        <span class="last"></span>
      </header>
      <div class="lane-body"></div>
    </article>`).join("");
  $("#bus").innerHTML = LANES.map((l) => `<span class="stop" data-lane="${l.key}" style="background:${laneColor(l.key)}"></span>`).join("");
}

function renderLanes(e, animate) {
  const current = cursor >= 0 ? visible[cursor] : -1;
  const prev = cursor > 0 ? events[visible[cursor - 1]] : null;
  const shownBefore = new Set(prev ? [prev.madeBy, ...prev.handledBy].filter((i) => i != null) : []);
  let richest = null;
  let richestScore = 0;
  for (const lane of LANES) {
    const el = document.querySelector(`.lane[data-lane="${lane.key}"]`);
    const rs = e ? roles(e, lane.key) : [];
    el.classList.toggle("active", rs.length > 0);
    el.classList.toggle("idle", rs.length === 0);
    const last = el.querySelector(".last");
    const body = el.querySelector(".lane-body");
    if (rs.length) {
      last.textContent = "";
      const { html, single, score } = laneBody(e, rs, lane.key, shownBefore);
      body.innerHTML = html;
      body.classList.toggle("single", single);
      if (score > richestScore) { richest = el; richestScore = score; }
    } else {
      const before = byLane[lane.key].filter((i) => i < current && !events[i].minor).pop();
      last.textContent = before == null ? "waiting" : `last at t = ${fmt(events[before].t)} s: ${events[before].text}`;
      el.querySelector(".lane-body").innerHTML = "";
    }
  }
  if (richest && animate) richest.scrollIntoView({ block: "nearest", behavior: "smooth" });
  placeBus(e, animate);
  if (animate) setTimeout(() => placeBus(e, false), 500); // after the smooth scroll
}

// A role is shown in full when it has something to explain; otherwise as one line.
function isCompact(r, e, shownBefore) {
  if (!r.h) return true;
  if (r.verb === "SENDS") return shownBefore.has(e.madeBy);
  return !r.h.out.length && !r.h.inside.length;
}

function inlinePath(h) {
  const names = h.calls.filter((c) => c.depth <= 2).map((c) => c.fn.split(".").pop());
  return names.slice(0, 5).join(" \u203a ") + (names.length > 5 ? " \u203a \u2026" : "");
}

function laneBody(e, rs, lane, shownBefore) {
  const what = [];
  const code = [];
  const panels = [];
  const seenPanels = new Set();
  let score = 0;
  for (const r of rs) {
    const target = r.verb === "SENDS" ? `to ${e.to.map(addressName).join(", ")}` : `from ${addressName(e.from)}`;
    const compact = isCompact(r, e, shownBefore);
    const path = r.h && compact
      ? `<div class="path">${r.verb === "SENDS" ? "made by the code shown in the previous step: " : "runs "}<code>${esc(inlinePath(r.h))}</code>${r.verb === "SENDS" ? "" : ", nothing to send"}</div>`
      : "";
    what.push(`
      <div><span class="verb">${r.verb}</span><span class="msg">${esc(e.type)}</span>
        ${r.verb === "TIMER" ? "" : `<span class="route">${esc(target)}</span>`}</div>${path}`);
    if (r.h && !compact) {
      score += 1 + r.h.inside.length * 3 + (r.verb === "SENDS" ? 0 : 1);
      const title = r.verb === "SENDS"
        ? `Code that produced it, on ${esc(r.h.trigger)}`
        : `Code that handled it in ${esc(addressName(r.h.module))}`;
      code.push(codeList(title, r.h.calls));
      for (const p of r.h.inside) {
        const key = JSON.stringify(p);
        if (!seenPanels.has(key)) { seenPanels.add(key); panels.push(panel(p, e)); }
      }
      if (r.verb !== "SENDS" && r.h.out.length) {
        what.push(`<div class="outs">then sends: ${r.h.out.map((o) => `<code>${esc(o)}</code>`).join(", ")}</div>`);
      }
    }
  }
  const chip = lane === "cabin" && e.car ? `<span class="car-chip">${esc(e.car)}</span> ` : "";
  const left = `
    <div class="what">
      ${what.join("")}
      <p class="text">${chip}${esc(e.text)}</p>
      ${panels.join("")}
    </div>`;
  return { html: code.length ? `${left}<div>${code.join("")}</div>` : left, single: !code.length, score };
}

function addressName(a) {
  if (!a) return "";
  if (a.startsWith("cabin:")) return `Cabin ${a.slice(6)}`;
  return { dispatch: "Dispatch", cc: "Control Center", passengers: "Passengers", world: "the scenario" }[a] ?? a;
}

function codeList(title, calls) {
  if (!calls.length) return "";
  const rows = calls.map((c) => {
    const name = c.fn.replace(/^(Dispatch|Cabin|ControlCenter|PassengerAgent|CommandCache)\./, "$1.");
    const count = c.n ? `<span class="n"> x${c.n}</span>` : "";
    return `
      <div class="call${KEY_FUNCTIONS.has(c.fn) ? " key" : ""}" style="padding-left:${c.depth * 0.9}rem">
        <span class="fn">${esc(name)}()${count}</span>
        <span class="loc" title="${esc(c.file)}:${c.line}">${esc(c.file)}:${c.line}</span>
        ${c.why ? `<span class="why">${esc(c.why)}</span>` : ""}
      </div>`;
  }).join("");
  return `<div class="code"><div class="title">${title}</div>${rows}</div>`;
}

// --- panels: what the code decided -------------------------------------------------------------

function panel(p, e) {
  const c = DATA.constants;
  switch (p.kind) {
    case "eta": {
      const max = Math.max(30, ...p.rows.map((r) => r.eta ?? 0));
      const rows = p.rows.map((r) => {
        const chosen = r.car === p.chosen;
        const out = r.eta == null;
        const val = r.eta != null ? `${fmt(r.eta)} s${chosen ? " ✓" : ""}`
          : r.excluded ? esc(r.excluded.toLowerCase().replace(/_/g, " ")) : "no legal route";
        const width = r.eta != null ? Math.max(4, (r.eta / max) * 100) : 100;
        return `<div class="eta-row${chosen ? " chosen" : ""}${out ? " out" : ""}">
          <span class="car-chip">${esc(r.car)}</span><span class="bar"><i style="width:${out ? 0 : width}%"></i></span>
          <span class="val" title="${esc(r.note ?? "")}">${val}</span></div>`;
      }).join("");
      const goods = story.cars.includes("G1")
        ? `<div class="quiet">G1 is the goods car: Dispatch only loops over P1-P4 for hall calls.</div>` : "";
      const chosen = p.rows.find((r) => r.car === p.chosen);
      let budget = "";
      if (chosen?.budget?.length) {
        budget = `<div class="budget"><h4>Promise check in <code>hall_route()</code>: DELAY_CAP = ${fmt(c.delayCap)} s</h4>` +
          chosen.budget.map((b) => {
            const delay = b.new - b.reference;
            const over = delay > c.delayCap + 1e-6;
            return `<div class="row"><span>floor ${b.floor} (${esc(b.ref)}): ETA t=${fmt(b.reference)} → t=${fmt(b.new)} s</span>
              <span class="meter" title="+${fmt(delay)} s of ${fmt(c.delayCap)} s"><i class="${over ? "over" : ""}" style="width:${Math.min(100, Math.max(0, delay) / c.delayCap * 100)}%"></i></span></div>
              <div class="quiet">delay +${fmt(delay)} s ${over ? "> cap: not allowed" : `≤ ${fmt(c.delayCap)} s: allowed`}</div>`;
          }).join("") + "</div>";
      }
      return `<div class="panel"><h4>ETA to ${esc(p.ref)}, computed by <code>route_etas()</code> for each car</h4>${rows}${goods}${budget}</div>`;
    }
    case "mission": {
      const max = Math.max(30, ...p.rows.map((r) => r.eta ?? 0));
      const rows = p.rows.map((r) => {
        const chosen = r.car === p.chosen;
        const out = r.excluded != null;
        const val = out ? esc(r.excluded.toLowerCase().replace(/_/g, " ")) : `${fmt(r.eta)} s${chosen ? " ✓" : ""}`;
        return `<div class="eta-row${chosen ? " chosen" : ""}${out ? " out" : ""}">
          <span class="car-chip">${esc(r.car)}</span><span class="bar"><i style="width:${out ? 0 : Math.max(4, r.eta / max * 100)}%"></i></span>
          <span class="val">${val}</span></div>`;
      }).join("");
      return `<div class="panel"><h4>Candidates from <code>mission_candidates()</code></h4>${rows}</div>`;
    }
    case "checks": {
      const items = p.items.map((it) => {
        const cls = p.failed == null || it.n < p.failed ? "" : it.n === p.failed ? "fail" : "skip";
        const extra = it.n === p.failed ? ` → NACK ${esc(p.nack)}` : "";
        return `<li class="${cls}"><span>${it.n}. ${esc(it.title)}${extra}</span></li>`;
      }).join("");
      const verdict = p.nack ? `<span class="no">NACK: ${esc(p.nack)}</span>` : `<span class="yes">all passed → ACK</span>`;
      return `<div class="panel"><h4>The Cabin's checks in <code>validate()</code></h4><ul class="checks">${items}</ul>
        <div class="verdict">${verdict}</div></div>`;
    }
    case "door": {
      const strip = DOOR_STATES.map((s) => `<span class="${s === p.to ? "to" : s === p.from ? "from" : ""}">${s}</span>`).join("<em>→</em>");
      return `<div class="panel"><h4>Door state machine <code>next_state()</code></h4>
        <div class="door-strip">${strip}</div>
        <div class="quiet">input ${esc(p.input)}: ${esc(p.from)} → ${esc(p.to)}</div></div>`;
    }
    case "hold":
      return `<div class="panel"><h4>Doors held open by <code>_must_hold_open()</code></h4>${loadBar(p.load)}
        <div class="verdict"><span class="no">${fmt(p.load, 0)} kg &gt; ${fmt(c.maxLoad, 0)} kg</span>: mode ${esc(p.mode)}, the car will not close or move.</div></div>`;
    case "blocked":
      return `<div class="panel"><h4>Motion refused by <code>_can_move()</code></h4>
        <div class="verdict"><span class="no">${esc(p.car)} may not move</span>: mode ${esc(p.mode)}</div></div>`;
    case "role":
      return `<div class="panel"><h4>Permission check <code>may_request()</code></h4>
        <div class="verdict">${esc(p.role)} may request ${esc(p.type)}: ${p.ok ? '<span class="yes">yes</span>' : '<span class="no">no</span>'}</div></div>`;
    case "request":
      return `<div class="panel"><h4>Request check <code>validate_request()</code></h4>
        <div class="verdict">${p.problem ? `<span class="no">rejected: ${esc(p.problem)}</span>` : '<span class="yes">well-formed</span> (floors, type, fields)'}</div></div>`;
    default:
      return "";
  }
}

function loadBar(load) {
  const c = DATA.constants;
  const scale = 1200;
  const pct = (v) => Math.min(100, (v / scale) * 100);
  const cls = load > c.maxLoad ? "over" : load >= c.fullLoad ? "full" : "";
  return `<div class="loadbar"><i class="${cls}" style="width:${pct(load)}%"></i>
    <b style="left:${pct(c.fullLoad)}%"></b><small style="left:${pct(c.fullLoad)}%;transform:translateX(-104%)">FULL ${c.fullLoad}</small>
    <b style="left:${pct(c.maxLoad)}%"></b><small style="left:${pct(c.maxLoad)}%;transform:translateX(4%)">MAX ${c.maxLoad}</small></div>`;
}

// --- the message bus column --------------------------------------------------------------------

function laneY(lane) {
  const head = document.querySelector(`.lane[data-lane="${lane}"] .lane-head`);
  const bus = $("#bus").getBoundingClientRect();
  const r = head.getBoundingClientRect();
  return Math.max(8, Math.min(bus.height - 8, r.top + r.height / 2 - bus.top));
}

function placeBus(e, animate) {
  for (const s of $$("#bus .stop")) s.style.top = `${laneY(s.dataset.lane)}px`;
  $$("#bus .pill").forEach((p) => p.remove());
  if (!e) return;
  const from = e.fromLane;
  const targets = [...new Set(e.handledBy.map((i) => story.handlings[i].lane))].filter((l) => l !== from || e.type === "Timer");
  const color = laneColor(from);
  for (const to of targets.length ? targets : [from]) {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = "✉";
    pill.title = e.type;
    pill.style.background = color;
    pill.style.top = `${laneY(animate ? from : to)}px`;
    $("#bus").appendChild(pill);
    if (animate) requestAnimationFrame(() => requestAnimationFrame(() => { pill.style.top = `${laneY(to)}px`; }));
  }
}

// --- caption and event line --------------------------------------------------------------------

function renderEvent(animate) {
  const e = cursor >= 0 ? events[visible[cursor]] : null;
  renderLanes(e, animate);
  let captioned = null;
  for (let i = cursor; i >= 0; i--) {
    const ev = events[visible[i]];
    if (ev.caption) { captioned = ev; break; }
  }
  const atKey = e && e.caption;
  $("#moment").innerHTML = captioned
    ? `${atKey ? '<span class="tag">KEY MOMENT</span>' : ""}${esc(captioned.caption)}`
    : '<span class="quiet">Press play, or step with the arrow keys.</span>';
  $("#event-line").innerHTML = e
    ? `<b>${cursor + 1}/${visible.length}</b> · t = ${fmt(e.t)} s · ${esc(addressName(e.from))} → ${esc(e.to.map(addressName).join(", "))} · <b>${esc(e.type)}</b>: ${esc(e.text)}`
    : `${visible.length} messages in this story`;
  const kinds = DATA.modules;
  $("#source").textContent = `recorded from the ${Object.values(kinds).every((k) => k === "real") ? "real" : "available"} modules · ${story.hiddenMessages} timers/heartbeats hidden`;
}

function buildTicks() {
  if (!story) return;
  $("#ticks").innerHTML = visible.map((i) => {
    const e = events[i];
    const left = (e.t / story.until) * 100;
    const color = e.caption ? css("--warn") : laneColor(e.fromLane);
    return `<i class="${e.caption ? "key" : ""}" style="left:${left}%;background:${color}"></i>`;
  }).join("");
}

// --- building ----------------------------------------------------------------------------------

const B = { top: 16, fh: 14, labelW: 22, callX: 30, waitX: 60, shaftX: 104, shaftW: 52, gap: 4 };

function shaftLeft(i) { return B.shaftX + i * (B.shaftW + B.gap); }
function floorTop(f) { return B.top + (DATA.constants.floors - f) * B.fh; }

function buildBuilding() {
  const svg = $("#building");
  const floors = DATA.constants.floors;
  const width = shaftLeft(story.cars.length) + 4;
  const height = B.top + floors * B.fh + 6;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const parts = [];
  for (let f = 1; f <= floors; f++) {
    const y = floorTop(f);
    if (f % 2 === 0) parts.push(`<rect x="0" y="${y}" width="${B.shaftX - 4}" height="${B.fh}" fill="var(--faint)" opacity="0.6"/>`);
    parts.push(`<text x="${B.labelW - 4}" y="${y + B.fh / 2 + 3}" text-anchor="end" font-size="8.5" fill="var(--muted)" font-family="var(--mono)">${f}</text>`);
  }
  story.cars.forEach((id, i) => {
    const x = shaftLeft(i);
    parts.push(`<rect x="${x}" y="${B.top}" width="${B.shaftW}" height="${floors * B.fh}" rx="3" fill="var(--shaft)"/>`);
    for (let f = 1; f < floors; f++) {
      parts.push(`<line x1="${x}" x2="${x + B.shaftW}" y1="${floorTop(f)}" y2="${floorTop(f)}" stroke="var(--shaft-line)" stroke-width="0.5"/>`);
    }
    parts.push(`<text x="${x + B.shaftW / 2}" y="${B.top - 5}" text-anchor="middle" font-size="10" font-weight="700" fill="var(--ink)">${id}</text>`);
  });
  parts.push(`<text x="${B.callX + 6}" y="${B.top - 5}" text-anchor="middle" font-size="8" fill="var(--muted)">calls</text>`);
  parts.push(`<text x="${B.waitX + 18}" y="${B.top - 5}" text-anchor="middle" font-size="8" fill="var(--muted)">waiting</text>`);
  svg.innerHTML = `<g>${parts.join("")}</g><g id="dyn"></g>`;
  $("#modes").innerHTML = Object.entries(MODE_LABEL).map(([m, label]) => `<span style="--m:${MODES[m]}">${label}</span>`).join("");
}

function frameAt(time) {
  const frames = story.frames;
  const i = Math.max(0, Math.min(frames.length - 1, Math.floor(time / story.frameS + 1e-9)));
  const j = Math.min(frames.length - 1, i + 1);
  const a = frames[i], b = frames[j];
  const span = b.t - a.t;
  const k = span > 0 ? Math.max(0, Math.min(1, (time - a.t) / span)) : 0;
  return { a, b, k };
}

function renderBuilding() {
  $("#clock").textContent = t.toFixed(1);
  $("#scrub").value = t;
  const { a, b, k } = frameAt(t);
  const e = cursor >= 0 ? events[visible[cursor]] : null;
  const focusCar = e?.car;
  const parts = [];
  // hall calls
  for (const [floor, dir, status, owner, id] of a.calls) {
    const y = floorTop(floor) + B.fh / 2;
    const x = B.callX + (dir === "UP" ? 0 : 12);
    const pending = status === "PENDING";
    const color = pending ? css("--warn") : laneColor("dispatch");
    const path = dir === "UP" ? `M${x - 4},${y + 3} L${x},${y - 4} L${x + 4},${y + 3} Z` : `M${x - 4},${y - 3} L${x},${y + 4} L${x + 4},${y - 3} Z`;
    parts.push(`<path d="${path}" fill="${color}" ${pending ? 'class="blink"' : ""}><title>${id} ${dir} ${status}${owner ? ` - ${owner}` : ""}</title></path>`);
  }
  // missions waiting at their origin
  for (const [id, type, status, car, origin] of a.missions) {
    if (origin == null || ["IN_PROGRESS", "COMPLETED", "REJECTED"].includes(status)) continue;
    const y = floorTop(origin) + B.fh / 2;
    parts.push(`<g transform="translate(${B.callX + 6},${y})"><rect x="-5" y="-1.6" width="10" height="3.2" fill="#d6336c"/><rect x="-1.6" y="-5" width="3.2" height="10" fill="#d6336c"/><title>${id} ${type} ${status}${car ? ` - ${car}` : ""}</title></g>`);
  }
  // waiting people
  for (const [key, count] of Object.entries(a.waiting)) {
    const floor = Number(key.slice(0, -1));
    const y = floorTop(floor);
    const shown = Math.min(count, 4);
    for (let n = 0; n < shown; n++) {
      const x = B.waitX + n * 8;
      parts.push(`<circle cx="${x + 3}" cy="${y + 3.5}" r="2" fill="var(--world)"/><rect x="${x + 0.8}" y="${y + 6}" width="4.4" height="6" rx="1.5" fill="var(--world)"/>`);
    }
    if (count > shown) parts.push(`<text x="${B.waitX + 33}" y="${y + 10}" font-size="8" fill="var(--muted)">+${count - shown}</text>`);
  }
  // cars
  story.cars.forEach((id, i) => {
    const ca = a.cars.find((c) => c.id === id);
    const cb = b.cars.find((c) => c.id === id) ?? ca;
    if (!ca) return;
    const pos = ca.pos + (cb.pos - ca.pos) * k;
    const x = shaftLeft(i);
    // stops of this car
    for (const [floor, kind] of ca.stops) {
      const y = floorTop(floor) + B.fh / 2;
      const sx = x + B.shaftW - 6;
      if (kind === "HALL_PICKUP") parts.push(`<circle cx="${sx}" cy="${y}" r="3.4" fill="none" stroke="${laneColor("dispatch")}" stroke-width="1.8"/>`);
      else if (kind === "CAR_DEST") parts.push(`<circle cx="${sx}" cy="${y}" r="3" fill="${laneColor("cabin")}"/>`);
      else parts.push(`<rect x="${sx - 3}" y="${y - 3}" width="6" height="6" transform="rotate(45 ${sx} ${y})" fill="#d6336c"/>`);
    }
    const y = floorTop(pos) + 1;
    const w = B.shaftW - 14;
    const cx = x + 3;
    const h = B.fh - 2;
    const color = MODES[ca.mode] ?? MODES.AUTO;
    const open = DOOR_OPEN[ca.door] ?? 0;
    const focus = id === focusCar;
    parts.push(`<g>`);
    if (focus) parts.push(`<rect x="${cx - 3}" y="${y - 3}" width="${w + 6}" height="${h + 6}" rx="4" fill="none" stroke="${css("--warn")}" stroke-width="2.5"/>`);
    parts.push(`<rect x="${cx}" y="${y}" width="${w}" height="${h}" rx="2" fill="${color}"/>`);
    if (open > 0) parts.push(`<rect x="${cx + w / 2 - (w / 2 - 2) * open}" y="${y + 1.5}" width="${(w - 4) * open}" height="${h - 3}" fill="#fdf6d8"/>`);
    parts.push(`<text x="${cx + w / 2}" y="${y + h / 2 + 3}" text-anchor="middle" font-size="7.5" font-weight="700" fill="${open > 0.6 ? "#333" : "#fff"}">${ca.pax || ""}</text>`);
    const load = Math.min(1, ca.load / 1200);
    if (ca.load > 0) {
      parts.push(`<rect x="${cx}" y="${y + h + 1}" width="${w}" height="2.5" fill="var(--shaft-line)"/><rect x="${cx}" y="${y + h + 1}" width="${w * load}" height="2.5" fill="${ca.load > DATA.constants.maxLoad ? "#ff6b6b" : ca.load >= DATA.constants.fullLoad ? "#f0a640" : "#7ee0d6"}"/>`);
    }
    if (ca.dir !== "IDLE") {
      const ax = x + B.shaftW - 6;
      const ay = y + h / 2;
      parts.push(ca.dir === "UP" ? `<path d="M${ax - 3},${ay + 2} L${ax},${ay - 3} L${ax + 3},${ay + 2} Z" fill="#fff"/>` : `<path d="M${ax - 3},${ay - 2} L${ax},${ay + 3} L${ax + 3},${ay - 2} Z" fill="#fff"/>`);
    }
    if (ca.faults.length) parts.push(`<text x="${cx + w + 4}" y="${y - 2}" font-size="11" font-weight="700" fill="#ff6b6b">!</text>`);
    parts.push(`<title>${id}: floor ${fmt(pos, 1)}, ${ca.mode}, doors ${ca.door}, ${ca.load} kg, route v${ca.rv}</title></g>`);
  });
  $("#dyn").innerHTML = parts.join("");
}

// --- playback ----------------------------------------------------------------------------------

function play() {
  if (!story) return;
  if (cursor >= visible.length - 1 && t >= story.until - 1e-6) { cursor = -1; t = 0; renderEvent(false); }
  playing = true;
  holdUntil = 0;
  $("#play").innerHTML = "&#10074;&#10074;";
  $("#play").setAttribute("aria-label", "Pause");
  lastFrame = performance.now();
  requestAnimationFrame(loop);
}

function pause() {
  playing = false;
  $("#play").innerHTML = "&#9654;";
  $("#play").setAttribute("aria-label", "Play");
}

function loop(now) {
  if (!playing) return;
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  if (now < holdUntil) { requestAnimationFrame(loop); return; }
  const nextIndex = cursor + 1 < visible.length ? visible[cursor + 1] : null;
  const nextT = nextIndex == null ? story.until : events[nextIndex].t;
  const gap = nextT - t;
  const rate = speed * (gap > 4 ? 8 : 2); // simulated seconds per real second
  t = Math.min(nextT, t + dt * rate);
  if (nextIndex != null && t >= nextT - 1e-9) {
    cursor += 1;
    const e = events[visible[cursor]];
    renderEvent(true);
    holdUntil = now + (e.minor ? 250 : e.caption ? 2400 : 950) / speed;
    if (e.caption && e.pause && $("#autopause").checked) pause();
  } else if (nextIndex == null && t >= story.until - 1e-9) {
    pause();
  }
  renderBuilding();
  if (playing) requestAnimationFrame(loop);
}

function step(delta) {
  pause();
  cursor = Math.max(-1, Math.min(visible.length - 1, cursor + delta));
  t = cursor >= 0 ? events[visible[cursor]].t : 0;
  renderEvent(delta > 0);
  renderBuilding();
}

function scrubTo(value) {
  pause();
  t = Number(value);
  cursor = lastVisibleAtTime(t);
  renderEvent(false);
  renderBuilding();
}

// --- wiring ------------------------------------------------------------------------------------

function init() {
  if (!DATA) {
    document.body.innerHTML = "<p style='padding:2rem'>web/demo/stories.js is missing. Run <code>python -m app.demo</code>.</p>";
    return;
  }
  buildStoryTabs();
  buildLanes();
  $("#play").addEventListener("click", () => (playing ? pause() : play()));
  $("#next").addEventListener("click", () => step(1));
  $("#prev").addEventListener("click", () => step(-1));
  $("#speed").addEventListener("change", (ev) => { speed = Number(ev.target.value); });
  $("#scrub").addEventListener("input", (ev) => scrubTo(ev.target.value));
  $("#reports").addEventListener("change", () => { computeVisible(); renderEvent(false); });
  window.addEventListener("resize", () => placeBus(cursor >= 0 ? events[visible[cursor]] : null, false));
  document.addEventListener("keydown", (ev) => {
    if (ev.target.closest("input[type=range], select")) return;
    if (ev.key === " ") { ev.preventDefault(); playing ? pause() : play(); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); step(1); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); step(-1); }
    else if (/^[1-6]$/.test(ev.key)) selectStory(Number(ev.key) - 1);
  });
  const fromHash = () => Math.max(0, DATA.stories.findIndex((s) => `#${s.key}` === location.hash));
  window.addEventListener("hashchange", () => {
    if (DATA.stories[fromHash()] !== story) selectStory(fromHash());
  });
  selectStory(fromHash());
}

init();
