"use strict";

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const PAGE_SIZES = [24, 48, 96, 120]; // Telescopius caps results_per_page at 120
const DEFAULT_PAGE_SIZE = 48;

const TYPE_OPTIONS = [
  ["DEEP_SKY_OBJECT", "Ciel profond (tout)"],
  ["gxy", "Galaxies"],
  ["eneb,rneb,pneb,snr", "Nébuleuses (toutes)"],
  ["eneb", "Nébuleuses en émission"],
  ["rneb", "Nébuleuses par réflexion"],
  ["pneb", "Nébuleuses planétaires"],
  ["snr", "Rémanents de supernova"],
  ["opcl,glcl", "Amas d'étoiles"],
  ["planet", "Planètes"],
  ["COMET", "Comètes"],
  ["", "Tout, système solaire compris"],
];
const MOON_OPTIONS = [
  ["", "Ignorer la Lune"],
  ["lrgb", "Éviter la Lune (LRGB / large bande)"],
  ["narrowband", "Éviter la Lune (bande étroite)"],
  ["ha_s2", "Éviter la Lune (Hα / SII)"],
  ["o3", "Éviter la Lune (OIII)"],
];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

let state = null;           // last /ui/api/state
let currentTab = "tonight";
let lastResults = [];
let lastNight = null;
let currentPage = 1;
let pagerLoad = null; // loads another page of the current paged view
let requestSeq = 0;

// ---------------------------------------------------------------- helpers
async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, v);
  }
  q.set("tz", TZ);
  return q.toString();
}

function formatRA(deg) {
  let s = Math.round(((deg / 15) % 24) * 36000) / 10; // tenths of time seconds
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return `${String(h % 24).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${s.toFixed(1).padStart(4, "0")}s`;
}

function formatDec(deg) {
  const sign = deg < 0 ? "−" : "+";
  let s = Math.round(Math.abs(deg) * 3600);
  const d = Math.floor(s / 3600); s -= d * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return `${sign}${String(d).padStart(2, "0")}° ${String(m).padStart(2, "0")}′ ${String(s).padStart(2, "0")}″`;
}

function formatSize(arcmin) {
  if (arcmin == null) return null;
  return arcmin >= 60 ? `${(arcmin / 60).toFixed(1)}°` : `${arcmin.toFixed(arcmin < 10 ? 1 : 0)}′`;
}

function localTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function since(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `il y a ${s} s`;
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  return `à ${localTime(iso)}`;
}

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function setStatus(text, isError = false) {
  const s = $("#status");
  s.textContent = text || "";
  s.classList.toggle("err", isError);
}

// ---------------------------------------------------------------- state
async function refreshState() {
  try {
    state = await api("/ui/api/state");
  } catch (e) {
    $("#nina-status").textContent = "Bridge injoignable";
    $("#nina-status").className = "pill err";
    return;
  }
  renderWarnings();
  renderSelection();
  renderNinaStatus();
  markSelectedCards();
}

function renderNinaStatus() {
  const pill = $("#nina-status");
  const last = state.nina_activity[0];
  if (!last) {
    pill.textContent = "NINA : jamais contacté";
    pill.className = "pill";
    pill.title = "Aucune requête reçue de NINA depuis le démarrage du bridge";
    return;
  }
  const age = (Date.now() - new Date(last.time).getTime()) / 1000;
  pill.textContent = `NINA : contacté ${since(last.time)}`;
  pill.className = "pill" + (age < 600 ? " ok" : "");
  pill.title = state.nina_activity
    .map((a) => `${localTime(a.time)}  ${a.path}  → ${a.status}`)
    .join("\n");
}

function renderWarnings() {
  const box = $("#warnings");
  box.replaceChildren();
  const c = state.config;
  const add = (text) => {
    const w = el("div", text, "warning");
    const b = el("button", "Ouvrir les réglages");
    b.addEventListener("click", openSettings);
    w.append(b);
    box.append(w);
  };
  if (!c.has_api_key) add("Aucune clé API Telescopius configurée.");
  if (c.latitude == null || c.longitude == null) {
    add("Position du site non configurée : les visibilités sont calculées d'après ton IP et NINA ne pourra pas importer le site.");
  }
}

function renderSelection() {
  const box = $("#selection");
  const sel = state.selection;
  box.classList.toggle("empty", !sel);
  box.classList.toggle("has", !!sel);
  if (!sel) return;
  const t = sel.target;
  $("#sel-thumb").src = t.thumbnail_url || "";
  $("#sel-thumb").onerror = () => { $("#sel-thumb").src = ""; };
  $("#sel-name").textContent = sel.nina_name;
  const meta = [t.id && t.id !== sel.nina_name ? t.id : null, t.type_label, t.source].filter(Boolean);
  $("#sel-meta").textContent = meta.join(" · ");
  $("#sel-coords").textContent =
    `RA ${formatRA(t.ra)}   Dec ${formatDec(t.dec)}   (J2000)` + (t.moving ? "   — position à l'heure de la recherche" : "");
  if (rotationTimer) return; // a local rotation edit is about to be pushed
  const rotEnabled = sel.rotation != null;
  if (document.activeElement !== $("#rot-value")) {
    const pa = rotEnabled ? sel.rotation : t.position_angle;
    $("#rot-value").value = pa == null ? "" : Number((((pa % 360) + 360) % 360).toFixed(2));
  }
  $("#rot-enabled").checked = rotEnabled;
  $("#rot-value").disabled = !rotEnabled;
}

function markSelectedCards() {
  const key = state && state.selection ? state.selection.target.key : null;
  for (const card of $$(".card")) {
    const isSel = key !== null && card.dataset.key === key;
    card.classList.toggle("selected", isSel);
    $(".select-btn", card).textContent = isSel ? "✓ Envoyée à NINA" : "Envoyer à NINA";
  }
}

// ---------------------------------------------------------------- selection
async function selectTarget(target) {
  try {
    state = await api("/ui/api/selection", {
      method: "POST",
      body: { target, rotation: target.position_angle ?? null },
    });
    renderSelection();
    markSelectedCards();
  } catch (e) {
    setStatus(`Sélection impossible : ${e.message}`, true);
  }
}

let rotationTimer = null;
function pushRotation() {
  clearTimeout(rotationTimer);
  rotationTimer = setTimeout(async () => {
    const enabled = $("#rot-enabled").checked;
    const raw = $("#rot-value").value;
    const rotation = enabled ? (raw === "" ? 0 : Number(raw)) : null;
    try {
      state = await api("/ui/api/selection/rotation", { method: "POST", body: { rotation } });
      rotationTimer = null;
      renderSelection();
    } catch (e) {
      rotationTimer = null;
      setStatus(`Rotation non enregistrée : ${e.message}`, true);
    }
  }, 350);
}


// ---------------------------------------------------------------- altitude chart
const SVG_NS = "http://www.w3.org/2000/svg";
const CHART = { height: 112, top: 16, bottom: 18, left: 26, right: 8 };
const SUN_SET_ALT = -0.833;
const DARK_SUN_ALT = -18;

function svg(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.append(e);
  return e;
}

function skyClass(sunAlt) {
  if (sunAlt > SUN_SET_ALT) return "day";
  if (sunAlt > DARK_SUN_ALT) return "twilight";
  return "dark";
}
const SKY_LABEL = { day: "Jour", twilight: "Crépuscule", dark: "Nuit noire" };

function minAltitude() {
  const form = currentTab === "search" ? $("#form-search") : $("#form-tonight");
  const v = Number(form.min_alt.value);
  return currentTab !== "lists" && form.min_alt.value !== "" && v >= 0 && v <= 90 ? v : 30;
}

function hhmm(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

let tooltipEl = null;
function tooltip() {
  if (!tooltipEl) {
    tooltipEl = el("div", null, "chart-tip hidden");
    tooltipEl.setAttribute("role", "status");
    document.body.append(tooltipEl);
  }
  return tooltipEl;
}

/** Fills `box` with the altitude-over-the-night chart of `t` and its text summary. */
function drawAltitudeChart(box, t, night, minAlt) {
  box.replaceChildren();
  const curve = t.altitude_curve;
  const n = curve.length;
  const W = Math.max(200, Math.floor(box.clientWidth));
  const { height: H, top, bottom, left, right } = CHART;
  const plotW = W - left - right;
  const plotH = H - top - bottom;
  const start = new Date(night.start).getTime();
  const stepMs = night.step_minutes * 60000;
  const timeAt = (i) => new Date(start + i * stepMs);
  const x = (i) => left + (i / (n - 1)) * plotW;
  const y = (alt) => top + ((90 - Math.min(90, Math.max(0, alt))) / 90) * plotH;

  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "alt-chart", tabindex: 0 });

  // Sky bands (context): consecutive samples of the same class merged into one rect.
  let bandStart = 0;
  for (let i = 1; i <= n; i++) {
    const cls = skyClass(night.sun_alt[bandStart]);
    if (i < n && skyClass(night.sun_alt[i]) === cls) continue;
    if (cls !== "dark") {
      const x0 = bandStart === 0 ? left : (x(bandStart - 1) + x(bandStart)) / 2;
      const x1 = i === n ? left + plotW : (x(i - 1) + x(i)) / 2;
      svg("rect", { x: x0, y: top, width: Math.max(0, x1 - x0), height: plotH, class: `band band-${cls}` }, root);
    }
    bandStart = i;
  }

  // Recessive frame: horizon baseline, threshold, y labels.
  svg("line", { x1: left, x2: left + plotW, y1: y(0), y2: y(0), class: "axis" }, root);
  svg("line", { x1: left, x2: left + plotW, y1: y(minAlt), y2: y(minAlt), class: "threshold" }, root);
  for (const [alt, cls] of [[0, ""], [minAlt, " ylabel-threshold"], [90, ""]]) {
    if (alt !== 0 && alt !== 90 && (Math.abs(alt - 90) < 12 || alt < 12)) continue;
    const label = svg("text", { x: left - 4, y: y(alt) + 3, class: "ylabel" + cls }, root);
    label.textContent = `${alt}°`;
  }

  // Hour ticks in local time, every 2 h (3 h if cramped).
  const hours = (n - 1) * stepMs / 3600000;
  const every = plotW / hours < 16 ? 3 : 2;
  const first = new Date(start);
  first.setMinutes(0, 0, 0);
  for (let ts = first.getTime() + 3600000; ts <= start + (n - 1) * stepMs; ts += 3600000) {
    const d = new Date(ts);
    if (d.getHours() % every !== 0) continue;
    const xi = left + ((ts - start) / stepMs / (n - 1)) * plotW;
    if (xi < left + 8 || xi > left + plotW - 8) continue;
    svg("line", { x1: xi, x2: xi, y1: y(0), y2: y(0) + 3, class: "axis" }, root);
    const label = svg("text", { x: xi, y: H - 4, class: "xlabel" }, root);
    label.textContent = `${d.getHours()}h`;
  }

  // Altitude line (2px) + 10% wash, drawn only above the horizon.
  const segments = [];
  let seg = null;
  for (let i = 0; i < n; i++) {
    const above = curve[i] >= 0;
    if (above && !seg) {
      seg = [];
      if (i > 0) { // enter at the interpolated horizon crossing
        const f = -curve[i - 1] / (curve[i] - curve[i - 1]);
        seg.push([x(i - 1) + f * (x(i) - x(i - 1)), y(0)]);
      }
    }
    if (above) seg.push([x(i), y(curve[i])]);
    if (seg && (!above || i === n - 1)) {
      if (!above) {
        const f = curve[i - 1] / (curve[i - 1] - curve[i]);
        seg.push([x(i - 1) + f * (x(i) - x(i - 1)), y(0)]);
      }
      segments.push(seg);
      seg = null;
    }
  }
  for (const s of segments) {
    const pts = s.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
    svg("polygon", { points: `${s[0][0].toFixed(1)},${y(0)} ${pts} ${s[s.length - 1][0].toFixed(1)},${y(0)}`, class: "alt-area" }, root);
    svg("polyline", { points: pts, class: "alt-line" }, root);
  }

  // "Now" marker.
  const nowIdx = (Date.now() - start) / stepMs;
  if (nowIdx >= 0 && nowIdx <= n - 1) {
    const xn = left + (nowIdx / (n - 1)) * plotW;
    svg("line", { x1: xn, x2: xn, y1: top, y2: y(0), class: "now" }, root);
  }

  // Direct label on the highest point once the Sun is down (the window starts
  // an hour before sunset, so an object setting in the evening peaks in daylight).
  let peak = -1;
  for (let i = 0; i < n; i++) {
    if (night.sun_alt[i] > SUN_SET_ALT) continue;
    if (peak < 0 || curve[i] > curve[peak]) peak = i;
  }
  if (peak < 0) peak = curve.indexOf(Math.max(...curve));
  if (curve[peak] > 0) {
    const px = x(peak), py = y(curve[peak]);
    svg("circle", { cx: px, cy: py, r: 4, class: "peak" }, root);
    const label = svg("text", { x: Math.min(Math.max(px, left + 34), left + plotW - 34), y: py - 7, class: "peak-label" }, root);
    label.textContent = `${Math.round(curve[peak])}° · ${hhmm(timeAt(peak))}`;
  }

  // Hover / keyboard layer: crosshair snapped to the nearest sample.
  const cross = svg("line", { y1: top, y2: y(0), class: "crosshair hidden" }, root);
  const dot = svg("circle", { r: 4, class: "cross-dot hidden" }, root);
  let focusIdx = null;
  const show = (i, clientX, clientY) => {
    i = Math.max(0, Math.min(n - 1, i));
    focusIdx = i;
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.classList.remove("hidden");
    dot.classList.toggle("hidden", curve[i] < 0);
    dot.setAttribute("cx", x(i));
    dot.setAttribute("cy", y(curve[i]));
    const tip = tooltip();
    tip.replaceChildren(
      el("strong", curve[i] < 0 ? "sous l'horizon" : `${Math.round(curve[i])}°`),
      el("span", `${hhmm(timeAt(i))} · ${SKY_LABEL[skyClass(night.sun_alt[i])]}`),
    );
    tip.classList.remove("hidden");
    const rect = root.getBoundingClientRect();
    const tx = clientX ?? rect.left + x(i);
    const ty = clientY ?? rect.top + y(Math.max(0, curve[i]));
    const tw = tip.offsetWidth;
    tip.style.left = `${Math.min(window.innerWidth - tw - 8, Math.max(8, tx + 12))}px`;
    tip.style.top = `${Math.max(8, ty - 44) + window.scrollY}px`;
  };
  const hide = () => {
    cross.classList.add("hidden");
    dot.classList.add("hidden");
    tooltip().classList.add("hidden");
  };
  root.addEventListener("pointermove", (ev) => {
    const rect = root.getBoundingClientRect();
    show(Math.round(((ev.clientX - rect.left - left) / plotW) * (n - 1)), ev.clientX, ev.clientY);
  });
  root.addEventListener("pointerleave", hide);
  root.addEventListener("focus", () => show(nowIdx >= 0 && nowIdx <= n - 1 ? Math.round(nowIdx) : peak));
  root.addEventListener("blur", hide);
  root.addEventListener("keydown", (ev) => {
    const delta = { ArrowLeft: -1, ArrowRight: 1, PageDown: -6, PageUp: 6 }[ev.key];
    if (delta) { ev.preventDefault(); show((focusIdx ?? peak) + delta); }
    if (ev.key === "Escape") hide();
  });

  // Text summary: the chart's key numbers stay readable without hovering.
  // Without astronomical darkness (high latitude summer) count from sunset.
  const hasDark = night.sun_alt.some((a) => a <= DARK_SUN_ALT);
  const sunLimit = hasDark ? DARK_SUN_ALT : SUN_SET_ALT;
  const when = hasDark ? "en nuit noire" : "Soleil couché (pas de nuit noire)";
  let firstOk = -1, lastOk = -1, okSamples = 0;
  for (let i = 0; i < n; i++) {
    if (curve[i] >= minAlt && night.sun_alt[i] <= sunLimit) {
      if (firstOk < 0) firstOk = i;
      lastOk = i;
      okSamples++;
    }
  }
  const hoursOk = (okSamples * night.step_minutes) / 60;
  const summary = el("div", null, "chart-summary");
  if (okSamples) {
    summary.append(`≥ ${minAlt}° ${when} : `, el("b", `${hhmm(timeAt(firstOk))} → ${hhmm(timeAt(lastOk))}`),
      ` (${hoursOk.toFixed(1)} h)`);
  } else {
    summary.append(el("span", `Jamais au-dessus de ${minAlt}° ${when}`, "low"));
  }
  root.setAttribute("role", "img");
  root.setAttribute("aria-label", `Altitude de ${t.id || t.common_name} pendant la nuit. Maximum ${Math.round(curve[peak])}° à ${hhmm(timeAt(peak))}. ${summary.textContent}.`);
  box.append(root, summary);
}

function renderChartLegend(night, minAlt) {
  const box = $("#chart-legend");
  box.replaceChildren();
  box.classList.toggle("hidden", !night);
  if (!night) return;
  const item = (keyCls, text) => {
    const s = el("span", null, "legend-item");
    s.append(el("i", null, `key ${keyCls}`), text);
    box.append(s);
  };
  item("key-line", "Altitude de la cible");
  item("key-threshold", `Altitude min (${minAlt}°)`);
  item("key-now", "Maintenant");
  item("key-band band-day", "Jour");
  item("key-band band-twilight", "Crépuscule");
  item("key-band band-dark", "Nuit noire (Soleil < −18°)");
  const ev = night.events;
  if (ev.astronomical_dusk && ev.astronomical_dawn) {
    box.append(el("span", `Nuit noire ${hhmm(new Date(ev.astronomical_dusk))} → ${hhmm(new Date(ev.astronomical_dawn))}`, "legend-note"));
  }
}

let chartJobs = [];
function drawCharts() {
  const minAlt = minAltitude();
  renderChartLegend(lastNight, minAlt);
  for (const [box, t] of chartJobs) drawAltitudeChart(box, t, lastNight, minAlt);
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawCharts, 150);
});

// ---------------------------------------------------------------- results
function renderResults(targets, night = null) {
  lastResults = targets;
  lastNight = night;
  chartJobs = [];
  const grid = $("#results");
  grid.replaceChildren();
  const tpl = $("#card-template");
  for (const t of targets) {
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.dataset.key = t.key;
    const img = $(".thumb img", card);
    const noImage = () => {
      img.remove();
      $(".thumb", card).classList.add("noimg");
      $(".thumb", card).textContent = t.family === "solar_system_object" ? "🪐" : "✦";
    };
    if (t.thumbnail_url) {
      img.addEventListener("error", noImage, { once: true });
      img.src = t.thumbnail_url;
      img.alt = t.id;
    } else {
      noImage();
    }
    $(".card-title", card).textContent = t.id || t.common_name;
    $(".card-sub", card).textContent = t.id ? t.common_name : "";

    const meta = $(".card-meta", card);
    for (const part of [
      t.type_label,
      t.constellation,
      t.mag != null ? `mag ${t.mag}` : null,
      formatSize(t.size_arcmin),
      t.position_angle != null ? `PA ${t.position_angle}°` : null,
    ]) if (part) meta.append(el("span", part));

    if (night && t.altitude_curve) {
      const chartBox = el("div", null, "card-chart");
      meta.after(chartBox);
      chartJobs.push([chartBox, t]);
    }
    $(".card-tonight", card).append(...tonightParts(t));
    if (t.notes) $(".card-notes", card).textContent = t.notes;

    const link = $(".ext", card);
    if (t.page_url) link.href = t.page_url; else link.remove();
    $(".select-btn", card).addEventListener("click", () => selectTarget(t));
    grid.append(card);
  }
  drawCharts();
  markSelectedCards();
}

function tonightParts(t) {
  const parts = [];
  const add = (label, value, cls) => {
    const s = el("span", label + " ");
    const b = el("b", value);
    if (cls) b.className = cls;
    s.append(b);
    parts.push(s);
  };
  if (t.tonight) {
    const n = t.tonight;
    if (n.rise || n.set) add("↑↓", `${n.rise || "—"} / ${n.set || "—"}`);
    if (n.transit) add("Transit", n.transit);
    if (n.max_alt != null) add("Haut. max", `${n.max_alt}°`, n.max_alt < 30 ? "low" : "");
    if (n.imaging_hours != null) add("Pose", `${n.imaging_hours} h`, n.imaging_hours < 1 ? "low" : "");
    if (n.moon_distance != null) add("Lune", `${Math.round(n.moon_distance)}°`);
  } else if (t.visibility) {
    const v = t.visibility;
    add("Haut. actuelle", `${v.alt_now}°`, v.alt_now < 20 ? "low" : "");
    add("Haut. max", `${v.max_alt}°`, v.max_alt < 30 ? "low" : "");
    add("Transit", localTime(v.transit_utc));
  }
  return parts;
}

async function runLoad(loader, label) {
  const seq = ++requestSeq;
  setStatus(`${label}…`);
  $("#pager").classList.add("hidden");
  try {
    const out = await loader();
    if (seq !== requestSeq) return; // a newer request superseded this one
    renderResults(out.targets, out.night);
    setStatus(out.summary);
    if (out.pager) renderPager(out.pager);
  } catch (e) {
    if (seq !== requestSeq) return;
    renderResults([]);
    setStatus(e.message, true);
  }
}

function formParams(form) {
  const params = {};
  for (const [k, v] of new FormData(form).entries()) if (v !== "") params[k] = v;
  return params;
}

function pageSummary(data, page, perPage, what) {
  if (!data.matched || !data.targets.length) return "Aucun résultat.";
  const from = (page - 1) * perPage + 1;
  return `${what} ${from}–${from + data.targets.length - 1} sur ${data.matched}.`;
}

function loadTonight(page = 1) {
  const params = formParams($("#form-tonight"));
  const perPage = Number(params.results_per_page) || DEFAULT_PAGE_SIZE;
  params.page = page;
  currentPage = page;
  pagerLoad = loadTonight;
  return runLoad(async () => {
    const data = await api("/ui/api/highlights?" + query(params));
    return {
      targets: data.targets,
      night: data.night,
      summary: pageSummary(data, page, perPage, "Cibles à leur meilleure période en ce moment :"),
      pager: { page, matched: data.matched, perPage },
    };
  }, "Chargement des cibles du moment");
}

function loadSearch(page = 1) {
  const params = formParams($("#form-search"));
  if (!params.name && !params.types && !params.min_alt) {
    params.min_alt = 30; // avoid listing the whole catalogue by magnitude
  }
  if (["imaging_time", "popularity", "size"].includes(params.order)) params.order_asc = 0;
  if (params.size_min) params.size_min = params.size_min * 60; // arcmin -> arcsec
  const perPage = Number(params.results_per_page) || DEFAULT_PAGE_SIZE;
  params.page = page;
  currentPage = page;
  pagerLoad = loadSearch;
  return runLoad(async () => {
    const data = await api("/ui/api/search?" + query(params));
    return {
      targets: data.targets,
      night: data.night,
      summary: pageSummary(data, page, perPage, "Résultats"),
      pager: { page, matched: data.matched, perPage },
    };
  }, "Recherche");
}

function renderPager({ page, matched, perPage }) {
  const pages = Math.max(1, Math.ceil(matched / perPage));
  $("#pager").classList.toggle("hidden", pages <= 1);
  $("#page-info").textContent = `Page ${page} / ${pages}`;
  $("#page-prev").disabled = page <= 1;
  $("#page-next").disabled = page >= pages;
}

async function loadLists(refresh = false) {
  const select = $("#list-select");
  try {
    const data = await api("/ui/api/lists" + (refresh ? "?refresh=1" : ""));
    const previous = select.value || localStorage.getItem("lastList") || "";
    select.replaceChildren();
    if (!data.lists.length) select.append(new Option("Aucune liste sur ce compte", ""));
    for (const l of data.lists) {
      select.append(new Option(`${l.name}${l.count != null ? ` (${l.count})` : ""}`, l.id));
    }
    if (data.lists.some((l) => l.id === previous)) select.value = previous;
    if (select.value) await loadList(refresh);
  } catch (e) {
    select.replaceChildren(new Option("—", ""));
    setStatus(e.message, true);
  }
}

function loadList(refresh = false) {
  const id = $("#list-select").value;
  if (!id) return;
  localStorage.setItem("lastList", id);
  return runLoad(async () => {
    const data = await api(`/ui/api/lists/${encodeURIComponent(id)}?` + query(refresh ? { refresh: 1 } : {}));
    return { targets: data.targets, night: data.night, summary: `${data.targets.length} cible(s) dans « ${data.name} ».` };
  }, "Chargement de la liste");
}

// ---------------------------------------------------------------- settings
function openSettings() {
  const f = $("#form-settings");
  const c = state.config;
  f.api_key.value = "";
  f.api_key.placeholder = c.has_api_key ? `inchangée (${c.api_key_hint})` : "colle ta clé ici";
  f.latitude.value = c.latitude ?? "";
  f.longitude.value = c.longitude ?? "";
  f.elevation.value = c.elevation ?? 0;
  f.location_name.value = c.location_name || "";
  for (const r of $$("input[name=name_format]", f)) r.checked = r.value === c.name_format;
  $("#settings-error").textContent = "";
  $("#settings").showModal();
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = $("#form-settings");
  const changes = {
    latitude: f.latitude.value,
    longitude: f.longitude.value,
    elevation: f.elevation.value || 0,
    location_name: f.location_name.value,
    name_format: (f.querySelector("input[name=name_format]:checked") || {}).value || "name",
  };
  if (f.api_key.value.trim()) changes.api_key = f.api_key.value.trim();
  try {
    state = await api("/ui/api/config", { method: "POST", body: changes });
    $("#settings").close();
    renderWarnings();
    renderSelection();
    reloadCurrentTab();
  } catch (e) {
    $("#settings-error").textContent = e.message;
  }
}

function geolocate() {
  const f = $("#form-settings");
  if (!navigator.geolocation) {
    $("#settings-error").textContent = "Géolocalisation non disponible dans ce navigateur.";
    return;
  }
  $("#settings-error").textContent = "Localisation en cours…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      f.latitude.value = pos.coords.latitude.toFixed(5);
      f.longitude.value = pos.coords.longitude.toFixed(5);
      if (pos.coords.altitude != null) f.elevation.value = Math.round(pos.coords.altitude);
      $("#settings-error").textContent = "";
    },
    (err) => { $("#settings-error").textContent = `Localisation refusée : ${err.message}`; },
    { enableHighAccuracy: false, timeout: 15000 },
  );
}

// ---------------------------------------------------------------- tabs
function switchTab(tab) {
  currentTab = tab;
  for (const b of $$(".tabs button")) b.classList.toggle("active", b.dataset.tab === tab);
  for (const p of $$(".panel")) p.classList.toggle("hidden", p.dataset.panel !== tab);
  reloadCurrentTab();
}

function reloadCurrentTab() {
  if (!state || !state.config.has_api_key) {
    renderResults([]);
    setStatus("Configure la clé API Telescopius pour afficher des cibles.");
    return;
  }
  if (currentTab === "tonight") loadTonight();
  else if (currentTab === "search") {
    renderResults([]);
    setStatus("Lance une recherche (sans nom : objets à plus de 30° ce soir).");
  } else loadLists();
}

// ---------------------------------------------------------------- init
function fillSelect(select, options) {
  for (const [value, label] of options) select.append(new Option(label, value));
}

function init() {
  for (const s of $$(".type-select")) fillSelect(s, TYPE_OPTIONS);
  for (const s of $$(".moon-select")) fillSelect(s, MOON_OPTIONS);
  const perPage = PAGE_SIZES.includes(Number(localStorage.getItem("perPage")))
    ? localStorage.getItem("perPage") : String(DEFAULT_PAGE_SIZE);
  for (const s of $$(".per-page-select")) {
    fillSelect(s, PAGE_SIZES.map((n) => [String(n), String(n)]));
    s.value = perPage;
  }
  $("#form-search").types.value = "";

  if (localStorage.getItem("night") === "1") document.body.classList.add("night");
  $("#night-toggle").addEventListener("click", () => {
    const on = document.body.classList.toggle("night");
    localStorage.setItem("night", on ? "1" : "0");
  });

  for (const b of $$(".tabs button")) b.addEventListener("click", () => switchTab(b.dataset.tab));
  $("#form-tonight").addEventListener("submit", (e) => { e.preventDefault(); loadTonight(); });
  $("#form-search").addEventListener("submit", (e) => { e.preventDefault(); loadSearch(1); });
  const goToPage = async (page) => {
    await pagerLoad(page);
    $("#status").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  $("#page-prev").addEventListener("click", () => goToPage(currentPage - 1));
  $("#page-next").addEventListener("click", () => goToPage(currentPage + 1));
  for (const s of $$(".per-page-select")) {
    s.addEventListener("change", () => {
      localStorage.setItem("perPage", s.value);
      for (const other of $$(".per-page-select")) other.value = s.value;
      if (currentTab === "tonight") loadTonight(1);
      else if (currentTab === "search" && lastResults.length) loadSearch(1);
    });
  }
  $("#list-select").addEventListener("change", () => loadList());
  $("#lists-refresh").addEventListener("click", () => loadLists(true));

  $("#rot-enabled").addEventListener("change", () => {
    $("#rot-value").disabled = !$("#rot-enabled").checked;
    pushRotation();
  });
  $("#rot-value").addEventListener("input", pushRotation);
  $("#sel-clear").addEventListener("click", async () => {
    state = await api("/ui/api/selection", { method: "DELETE" });
    renderSelection();
    markSelectedCards();
  });

  $("#open-settings").addEventListener("click", openSettings);
  $("#settings-cancel").addEventListener("click", () => $("#settings").close());
  $("#form-settings").addEventListener("submit", saveSettings);
  $("#geolocate").addEventListener("click", geolocate);

  refreshState().then(() => {
    reloadCurrentTab();
    if (!state.config.has_api_key) openSettings();
  });
  setInterval(refreshState, 5000);
}

init();
