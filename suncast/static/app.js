"use strict";

// ---- shared helpers -------------------------------------------------

function setStatus(msg, isError) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = isError ? "error" : "";
}

async function apiFetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail || detail;
    } catch (e) {
      /* body wasn't JSON */
    }
    const err = new Error(`${r.status}: ${detail}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

function fmtWh(v) {
  return `${Math.round(v)} Wh`;
}

function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// ---- svgChart: two polylines (raw vs corrected) from hourly series ----

function svgChart(hourly) {
  const w = 600;
  const h = 150;
  if (!hourly || hourly.length === 0) {
    return `<svg viewBox="0 0 ${w} ${h}"></svg>`;
  }
  const rawVals = hourly.map((p) => p[1]);
  const corrVals = hourly.map((p) => p[2]);
  const maxV = Math.max(1, ...rawVals, ...corrVals);
  const n = hourly.length;
  const x = (i) => (n <= 1 ? 0 : (i / (n - 1)) * w);
  const y = (v) => h - (v / maxV) * h;

  const toPoints = (idx) => hourly.map((p, i) => `${x(i)},${y(p[idx])}`).join(" ");

  // Axis labels: y in W (0..max), x in local time. xkcd 833 compliance.
  const padL = 40;
  const padB = 16;
  const t0 = new Date(hourly[0][0]);
  const t1 = new Date(hourly[n - 1][0]);
  const fmt = (t) => t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `<svg viewBox="${-padL} 0 ${w + padL + 8} ${h + padB}">
    <line x1="0" y1="0" x2="0" y2="${h}" stroke="#cbd5e1" stroke-width="1" />
    <line x1="0" y1="${h}" x2="${w}" y2="${h}" stroke="#cbd5e1" stroke-width="1" />
    <text x="-6" y="10" text-anchor="end" font-size="11" fill="#64748b">${Math.round(maxV)} W</text>
    <text x="-6" y="${h}" text-anchor="end" font-size="11" fill="#64748b">0</text>
    <text x="0" y="${h + 13}" font-size="11" fill="#64748b">${fmt(t0)}</text>
    <text x="${w}" y="${h + 13}" text-anchor="end" font-size="11" fill="#64748b">${fmt(t1)}</text>
    <polyline points="${toPoints(1)}" fill="none" stroke="#94a3b8" stroke-width="2" />
    <polyline points="${toPoints(2)}" fill="none" stroke="#2563eb" stroke-width="2" />
  </svg>`;
}

// ---- svgBars: forecast (raw) vs actual bar pairs from history days ----

function svgBars(days) {
  const w = 600;
  const h = 150;
  if (!days || days.length === 0) {
    return `<svg viewBox="0 0 ${w} ${h}"></svg>`;
  }
  const maxV = Math.max(1, ...days.map((d) => d.forecast_wh), ...days.map((d) => d.actual_wh));
  const n = days.length;
  const slot = w / n;
  const barW = Math.max(2, slot * 0.35);

  const bars = days
    .map((d, i) => {
      const cx = i * slot + slot / 2;
      const rawH = (d.forecast_wh / maxV) * h;
      const actH = (d.actual_wh / maxV) * h;
      const rawX = cx - barW - 1;
      const actX = cx + 1;
      return `<rect x="${rawX}" y="${h - rawH}" width="${barW}" height="${rawH}" fill="#94a3b8" />
        <rect x="${actX}" y="${h - actH}" width="${barW}" height="${actH}" fill="#2563eb" />`;
    })
    .join("");

  // Axis labels: y in Wh, x = first/last day. xkcd 833 compliance.
  const padL = 46;
  const padB = 16;
  return `<svg viewBox="${-padL} 0 ${w + padL + 8} ${h + padB}">
    <line x1="0" y1="0" x2="0" y2="${h}" stroke="#cbd5e1" stroke-width="1" />
    <line x1="0" y1="${h}" x2="${w}" y2="${h}" stroke="#cbd5e1" stroke-width="1" />
    <text x="-6" y="10" text-anchor="end" font-size="11" fill="#64748b">${Math.round(maxV)} Wh</text>
    <text x="-6" y="${h}" text-anchor="end" font-size="11" fill="#64748b">0</text>
    <text x="0" y="${h + 13}" font-size="11" fill="#64748b">${days[0].day.slice(5)}</text>
    <text x="${w}" y="${h + 13}" text-anchor="end" font-size="11" fill="#64748b">${days[n - 1].day.slice(5)}</text>
    ${bars}</svg>`;
}

// ---- index page: map + forecast --------------------------------------

let map = null;
let targetMarker = null;
let targetLatLng = null;
let currentLocation = null;

function setTarget(lat, lon) {
  targetLatLng = { lat, lon };
  if (targetMarker) {
    targetMarker.setLatLng([lat, lon]);
  } else {
    targetMarker = L.marker([lat, lon]).addTo(map);
  }
  document.getElementById("target-lat").textContent = lat.toFixed(4);
  document.getElementById("target-lon").textContent = lon.toFixed(4);
}

function initMap() {
  map = L.map("map").setView([48.77, 9.16], 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  map.on("click", (e) => setTarget(e.latlng.lat, e.latlng.lng));

  fetch("/api/current-location")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error("no location"))))
    .then((loc) => {
      currentLocation = loc;
      L.marker([loc.lat, loc.lon]).addTo(map).bindPopup("Current location");
      L.circle([loc.lat, loc.lon], { radius: loc.range_m, color: "#2563eb" }).addTo(map);
      map.setView([loc.lat, loc.lon], 10);
    })
    .catch(() => {
      /* no current location yet — skip silently */
    });

  document.getElementById("use-location").addEventListener("click", () => {
    if (!currentLocation) {
      setStatus("no current location available", true);
      return;
    }
    map.setView([currentLocation.lat, currentLocation.lon], 12);
    setTarget(currentLocation.lat, currentLocation.lon);
  });

  document.getElementById("forecast-btn").addEventListener("click", runForecast);
  document.getElementById("panel-form").addEventListener("submit", saveConfig);
  loadConfig();
}

async function loadConfig() {
  try {
    const cfg = await apiFetch("/api/config");
    for (const key of Object.keys(cfg)) {
      const input = document.querySelector(`#panel-form [name="${key}"]`);
      if (input) input.value = cfg[key];
    }
  } catch (e) {
    setStatus(`config load failed: ${e.message}`, true);
  }
}

async function saveConfig(evt) {
  evt.preventDefault();
  const form = evt.target;
  const body = {
    panel_wp: Number(form.panel_wp.value),
    tilt_deg: Number(form.tilt_deg.value),
    azimuth_deg: Number(form.azimuth_deg.value),
    charger_limit_w: Number(form.charger_limit_w.value),
    damping: Number(form.damping.value),
  };
  try {
    await apiFetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setStatus("panel config saved");
  } catch (e) {
    setStatus(`save failed: ${e.message}`, true);
  }
}

async function runForecast() {
  if (!targetLatLng) {
    setStatus("click the map to pick a location first", true);
    return;
  }
  const days = Number(document.getElementById("days").value);
  setStatus("loading forecast…");
  try {
    const json = await apiFetch("/api/forecast", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: targetLatLng.lat, lon: targetLatLng.lon, days }),
    });
    renderResults(json);
    setStatus("");
  } catch (e) {
    setStatus(`forecast failed: ${e.message}`, true);
  }
}

function renderResults(json) {
  const results = document.getElementById("results");
  const f = json.factor;
  const badge = f.calibrated ? "" : '<span class="badge">uncalibrated</span>';
  const factorLine = `<p>factor ${f.factor.toFixed(2)} (p25 ${f.p25.toFixed(2)} / p75 ${f.p75.toFixed(2)}, ${f.samples} samples) ${badge}</p>`;
  const chart = `<div class="card">${svgChart(json.hourly)}</div>`;

  const days = Object.keys(json.daily).sort();
  const cards = days
    .map((day) => {
      const d = json.daily[day];
      const bw = json.best_windows[day];
      const windowLine = bw
        ? `best window ${fmtTime(bw.start)}–${fmtTime(bw.end)} (${fmtWh(bw.wh)})`
        : "no best window";
      return `<div class="card day-card">
        <h3>${day}</h3>
        <div class="wh-line">raw ${fmtWh(d.raw_wh)} · corrected ${fmtWh(d.corrected_wh)} (${fmtWh(d.lower_wh)}–${fmtWh(d.upper_wh)})</div>
        <div class="wh-line">${windowLine}</div>
      </div>`;
    })
    .join("");

  results.innerHTML = factorLine + chart + cards;
}

// ---- history page -----------------------------------------------------

async function loadHistory() {
  try {
    const json = await apiFetch("/api/history?days=30");
    renderHistory(json);
  } catch (e) {
    setStatus(`history load failed: ${e.message}`, true);
  }
}

function renderHistory(json) {
  const body = document.getElementById("history-body");
  body.innerHTML = json.days
    .map(
      (d) =>
        `<tr><td>${d.day}</td><td>${fmtWh(d.forecast_wh)}</td><td>${fmtWh(d.actual_wh)}</td><td>${d.ratio.toFixed(2)}</td></tr>`
    )
    .join("");

  const m = json.metrics_raw;
  const f = json.factor;
  document.getElementById("summary").textContent =
    `MAE ${m.mae.toFixed(1)} Wh · MAPE ${m.mape_pct.toFixed(1)}% · bias ${m.bias_wh.toFixed(1)} Wh · ` +
    `n=${m.n} · current factor ${f.factor.toFixed(2)}`;

  document.getElementById("bars").innerHTML = svgBars(json.days);
}
