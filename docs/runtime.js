"use strict";

const runtimeField = name => document.getElementById("runtime-" + name);
const endpointPicker = {current: [], next: []};
const modelPicker = {current: [], next: []};
const runtimeTable = runtimeField("table");
let snapshot = null;
let currentView = "endpoints";
let refreshTimer;
let refreshVersion = 0;
let connected = false;

function chart() {
  const canvas = runtimeField("chart");
  const legend = runtimeField("legend");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const scale = devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * scale);
  canvas.height = Math.round(rect.height * scale);
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  const css = getComputedStyle(canvas);
  const color = name => css.getPropertyValue(name).trim();
  const width = rect.width, height = rect.height;
  const host = runtimeField("series").value === "host";
  const series = host ? [
    ["cpu", "Host CPU", "--accent"], ["memory", "Host memory", "--signal-secondary"],
  ] : [
    ["loaded", "Loaded models", "--accent"], ["busy", "Models in use", "--signal-tertiary"],
    ["active", "Active requests", "--signal-secondary"],
  ];
  legend.replaceChildren(...series.map(([, label, style]) => {
    const group = element("span", "");
    const swatch = element("i", "");
    swatch.style.setProperty("--series", color(style));
    group.append(swatch, document.createTextNode(label));
    return group;
  }));
  ctx.font = "11px ui-monospace, monospace";
  ctx.fillStyle = color("--muted");
  if (!snapshot?.samples.length) {
    ctx.fillText("Waiting for runtime samples", 16, height / 2);
    return;
  }
  const points = snapshot.samples;
  const peak = host ? 100 : Math.max(4, ...points.flatMap(point => series.map(([key]) => point[key] || 0)));
  const divisor = Math.max(1, Math.ceil(peak / 4));
  const ceiling = host ? 100 : divisor * 4;
  const left = host ? 44 : 32, right = width - 16, top = 13, bottom = height - 25;
  ctx.lineWidth = 1;
  for (let row = 0; row <= 4; row++) {
    const y = bottom - (bottom - top) * row / 4;
    ctx.strokeStyle = color("--line");
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.fillText(String(row * ceiling / 4) + (host ? "%" : ""), 8, y + 4);
  }
  const newest = points.at(-1).time;
  const oldest = newest - snapshot.window_seconds;
  const x = time => left + (right - left) * Math.max(0, Math.min(1, (time - oldest) / snapshot.window_seconds));
  const y = value => bottom - (bottom - top) * value / ceiling;
  for (const [key, , stroke] of series) {
    const coordinates = points.flatMap(point => point[key] === null || point[key] === undefined ? [] : [[x(point.time), y(point[key])]]);
    if (!coordinates.length) continue;
    ctx.beginPath();
    ctx.strokeStyle = color(stroke);
    ctx.lineWidth = 1.85;
    coordinates.forEach(([px, py], index) => index === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py));
    ctx.stroke();
    const [px, py] = coordinates.at(-1);
    ctx.beginPath();
    ctx.fillStyle = color(stroke);
    ctx.arc(px, py, 2.8, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = color("--muted");
  ctx.fillText("−" + snapshot.window_seconds / 60 + "m", left, height - 8);
  ctx.fillText("Now", right - 21, height - 8);
}

function hostChips(host, storage) {
  const container = runtimeField("host");
  if (!host) {
    container.replaceChildren();
    return;
  }
  const metrics = [
    ["CPU", (host.cpu === null ? "Sampling" : host.cpu + "%") + " · " + host.logical_cpus + " threads"],
    ["RAM", displayBytes(host.memory_used_bytes) + " / " + displayBytes(host.memory_bytes)],
    ...(storage ? [["Model disk", displayBytes(storage.free_bytes) + " free of " + displayBytes(storage.total_bytes) +
      " · " + storage.source + " mounted at " + storage.mount]] :
      [["Storage", displayBytes(host.filesystem_free_bytes) + " free"]]),
    ["Host", host.architecture],
  ];
  for (const gpu of host.gpus || []) {
    metrics.push([gpu.name, (gpu.utilization === null ? "" : gpu.utilization + "% · ")
      + displayBytes(gpu.memory_used_mib * 1024 ** 2) + " / " + displayBytes(gpu.memory_total_mib * 1024 ** 2)]);
  }
  container.replaceChildren(...metrics.map(([name, value]) => {
    const row = element("span", "host-chip");
    row.append(element("strong", "", name), document.createTextNode(" " + value));
    return row;
  }));
}

function renderTable() {
  if (!snapshot || !document.getElementById("activity").open) return;
  const search = runtimeField("search").value.trim().toLowerCase();
  const filter = runtimeField("state").value;
  const data = snapshot[currentView];
  const headers = currentView === "lifecycle" ? ["Time", "Subject", "State", "Detail"]
    : currentView === "models" ? ["Model", "State", "Active", "Calls", "Errors", "Mean"]
    : ["Endpoint", "Calls", "Errors", "Mean"];
  const rows = data.flatMap(item => {
    if (currentView === "lifecycle" && filter && item.state !== filter) return [];
    const mean = item.mean_ms === null || item.mean_ms === undefined ? "—" : item.mean_ms + " ms";
    const cells = currentView === "lifecycle"
      ? [new Date(item.time * 1000).toLocaleTimeString(), item.subject, stateLabels[item.state] || item.state, item.detail || "—"]
      : currentView === "models" ? [item.name, stateLabels[item.state] || item.state, item.active, item.calls, item.errors, mean]
      : [item.name, item.calls, item.errors, mean];
    return !search || cells.join(" ").toLowerCase().includes(search) ? [cells] : [];
  });
  if (!rows.length) {
    runtimeTable.replaceChildren(element("p", "empty-table", "No matching activity in this window."));
    return;
  }
  const table = element("table", "");
  const headings = table.createTHead().insertRow();
  headers.forEach(name => {
    const cell = element("th", "", name);
    cell.scope = "col";
    headings.append(cell);
  });
  const body = table.createTBody();
  for (const record of rows) {
    const line = body.insertRow();
    for (const value of record) line.insertCell().textContent = value;
  }
  const offset = runtimeTable.scrollTop;
  runtimeTable.replaceChildren(table);
  runtimeTable.scrollTop = offset;
}

function renderSnapshot(response) {
  snapshot = response;
  const latest = response.samples.at(-1);
  runtimeField("loaded").textContent = latest?.loaded ?? "—";
  runtimeField("busy").textContent = latest?.busy ?? "—";
  runtimeField("active").textContent = response.active_requests;
  runtimeField("clients").textContent = response.clients;
  runtimeField("client-detail").textContent = response.websockets + " open WebSockets · Clients seen in " + response.client_window_seconds + "s";
  const uptime = Math.max(0, Math.floor(Date.now() / 1000 - response.since));
  runtimeField("message").textContent = "Process up " + (uptime < 60 ? uptime + "s" : Math.floor(uptime / 60) + "m") + " · Metrics refresh every 4s";
  hostChips(response.host, response.model_storage);
  updateSelect(runtimeField("endpoint"), response.endpoint_options, endpointPicker, "All endpoints");
  updateSelect(runtimeField("model-filter"), response.model_options, modelPicker, "All models");
  document.dispatchEvent(new CustomEvent("voxrn:health", {detail: response.health}));
  chart();
  renderTable();
}

async function refreshRuntime() {
  clearTimeout(refreshTimer);
  if (!connected || document.hidden) return;
  const version = ++refreshVersion;
  const params = new URLSearchParams({window_seconds: runtimeField("window").value});
  const endpoint = runtimeField("endpoint").value;
  const model = runtimeField("model-filter").value;
  if (endpoint) params.set("endpoint", endpoint);
  if (model) params.set("model", model);
  try {
    const data = await fetchRuntime("/v1/control/runtime?" + params);
    if (version === refreshVersion) renderSnapshot(data);
  } catch (error) {
    if (version === refreshVersion) runtimeField("message").textContent = "Runtime metrics unavailable. " + error.message;
  } finally {
    if (version === refreshVersion) refreshTimer = setTimeout(refreshRuntime, 4000);
  }
}

document.addEventListener("voxrn:connected", () => {
  connected = true;
  refreshRuntime();
});
document.addEventListener("visibilitychange", refreshRuntime);
document.getElementById("activity").addEventListener("toggle", renderTable);
["window","endpoint","model-filter"].forEach(name => runtimeField(name).addEventListener("change", refreshRuntime));
runtimeField("series").addEventListener("change", chart);
runtimeField("search").addEventListener("input", renderTable);
runtimeField("state").addEventListener("change", renderTable);
document.querySelectorAll("[data-view]").forEach(button => {
  button.addEventListener("click", () => {
    currentView = button.dataset.view;
    document.querySelectorAll("[data-view]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    runtimeField("state-filter").hidden = currentView !== "lifecycle";
    renderTable();
  });
});
new ResizeObserver(chart).observe(runtimeField("chart").parentElement);
