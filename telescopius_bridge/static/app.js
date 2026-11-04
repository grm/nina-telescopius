"use strict";

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const PAGE_SIZE = 24;

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
let searchPage = 1;
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

// ---------------------------------------------------------------- results
function renderResults(targets) {
  lastResults = targets;
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

    $(".card-tonight", card).append(...tonightParts(t));
    if (t.notes) $(".card-notes", card).textContent = t.notes;

    const link = $(".ext", card);
    if (t.page_url) link.href = t.page_url; else link.remove();
    $(".select-btn", card).addEventListener("click", () => selectTarget(t));
    grid.append(card);
  }
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
    renderResults(out.targets);
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

function loadTonight(refresh = false) {
  const params = formParams($("#form-tonight"));
  if (refresh) params.refresh = 1;
  return runLoad(async () => {
    const data = await api("/ui/api/highlights?" + query(params));
    return {
      targets: data.targets,
      summary: `${data.targets.length} cible(s) à leur meilleure période en ce moment (${data.matched} correspondances).`,
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
  params.results_per_page = PAGE_SIZE;
  params.page = page;
  searchPage = page;
  return runLoad(async () => {
    const data = await api("/ui/api/search?" + query(params));
    return {
      targets: data.targets,
      summary: data.matched ? `${data.matched} résultat(s).` : "Aucun résultat.",
      pager: { page, matched: data.matched },
    };
  }, "Recherche");
}

function renderPager({ page, matched }) {
  const pages = Math.max(1, Math.ceil(matched / PAGE_SIZE));
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
    return { targets: data.targets, summary: `${data.targets.length} cible(s) dans « ${data.name} ».` };
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
  $("#form-search").types.value = "";

  if (localStorage.getItem("night") === "1") document.body.classList.add("night");
  $("#night-toggle").addEventListener("click", () => {
    const on = document.body.classList.toggle("night");
    localStorage.setItem("night", on ? "1" : "0");
  });

  for (const b of $$(".tabs button")) b.addEventListener("click", () => switchTab(b.dataset.tab));
  $("#form-tonight").addEventListener("submit", (e) => { e.preventDefault(); loadTonight(); });
  $("#form-search").addEventListener("submit", (e) => { e.preventDefault(); loadSearch(1); });
  $("#page-prev").addEventListener("click", () => loadSearch(searchPage - 1));
  $("#page-next").addEventListener("click", () => loadSearch(searchPage + 1));
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
