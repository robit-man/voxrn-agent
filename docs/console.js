"use strict";

const field = (name) => document.getElementById(`api-${name}`);
const form = field("console");
const operation = field("operation");
const statusLine = field("status");
const output = field("output");
let request;
let audioUrl;
let tunnelMode;
let runtimeIdentity;
let voices = [];
let capabilityModels = new Map();
let runtimeHealth = new Map();
const browserClient = crypto.randomUUID?.() || String(Date.now()) + Math.random().toString(36).slice(2);
const readyModelNames = { current: [], next: [] };

function option(parent, value, label = value) {
  parent.add(new Option(label, value));
}

function initialBody(config, examples) {
  if (config.type === "mcp") {
    return examples.mcp.find((example) => example.tool === config.tool)?.arguments || {};
  }
  const ref = config.schema?.$ref;
  if (ref) {
    const example = examples.http.find((item) => item.schema === ref);
    if (example) {
      const { file, ...values } = example.value;
      return values;
    }
  }
  if (config.path.endsWith("/transcriptions") && config.path.includes("{media_id}")) {
    return { model: "whisper-base" };
  }
  return {};
}

function serviceUrl() {
  const url = new URL(field("url").value);
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(loopback && url.protocol === "http:"))
      || url.username || url.password || url.search || url.hash) {
    throw new Error("Use HTTPS or an HTTP loopback URL without credentials, a query, or a fragment.");
  }
  return url.href.replace(/\/$/, "");
}

function clearResponse() {
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = null;
  field("audio").removeAttribute("src");
  field("audio").hidden = true;
  field("download").removeAttribute("href");
  field("download").hidden = true;
  output.textContent = "";
  if (field("artifact")) field("artifact").hidden = true;
}

function playAudio(audio) {
  audioUrl = URL.createObjectURL(audio);
  field("audio").src = audioUrl;
  field("audio").hidden = false;
  field("download").href = audioUrl;
  field("download").hidden = false;
}

function runtimeHeaders() {
  const headers = new Headers({"X-VoxRN-Client-ID": browserClient});
  const token = field("token").value.trim();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

async function fetchRuntime(path) {
  const response = await fetch(serviceUrl() + path, {
    headers: runtimeHeaders(), redirect: "error", cache: "no-store", signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error(`Runtime returned HTTP ${response.status}.`);
  return response.json();
}

function updateSelect(select, names, state, placeholder) {
  state.next = names;
  if (JSON.stringify(state.current) === JSON.stringify(names)) return;
  if (document.activeElement === select) {
    if (!state.waiting) {
      state.waiting = true;
      select.addEventListener("blur", () => {
        state.waiting = false;
        updateSelect(select, state.next, state, placeholder);
      }, {once: true});
    }
    return;
  }
  const selected = select.value;
  select.replaceChildren();
  if (placeholder) option(select, "", placeholder);
  names.forEach(name => option(select, name));
  if (names.includes(selected)) select.value = selected;
  state.current = names;
}

async function fetchCapabilities() {
  const limits = await fetchRuntime("/v1/capabilities");
  if (`voxrn-${limits.service}` !== runtimeIdentity) {
    throw new Error("This endpoint serves a different VoxRN API.");
  }
  capabilityModels = new Map(limits.models.map(row => [row.identity.model, row]));
  const names = limits.models.map(row => row.identity.model);
  updateSelect(field("model"), names, readyModelNames, "Use request body");
  document.dispatchEvent(new CustomEvent("voxrn:capabilities", { detail: limits }));
  return limits;
}

async function connect() {
  field("connect").disabled = true;
  const status = field("connection-status");
  status.textContent = "Connecting…";
  try {
    if (new URL(serviceUrl()).hostname.endsWith(".trycloudflare.com")) tunnelMode = "quick";
    const limits = await fetchCapabilities();
    if (limits.service === "tts") voices = (await fetchRuntime("/v1/voices")).voices;
    await refreshModels();
    document.dispatchEvent(new Event("voxrn:connected"));
    field("share").hidden = !tunnelMode;
    const note = tunnelMode === "quick" ? " · Quick Tunnel · SSE requires a named tunnel" : "";
    status.textContent = `Connected · ${limits.models.length} ready models${note}`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    field("connect").disabled = false;
  }
}

function selectRequestModel() {
  if (!field("model").value || field("body-label").hidden) return;
  try {
    const body = JSON.parse(field("body").value);
    if (!Object.hasOwn(body, "model")) return;
    body.model = field("model").value;
    if (Object.hasOwn(body, "voice") && voices.length) {
      const matches = voices.filter(voice => voice.models.includes(body.model));
      if (matches.length && !matches.some(voice => voice.id === body.voice)) body.voice = matches[0].id;
    }
    field("body").value = JSON.stringify(body, null, 2);
  } catch { /* The editor reports invalid JSON if submitted. */ }
}

field("model").addEventListener("change", selectRequestModel);
field("connect").addEventListener("click", connect);
field("share").addEventListener("click", async () => {
  try {
    const url = new URL(location.href);
    url.hash = new URLSearchParams({endpoint: serviceUrl(), key: field("token").value.trim(), transport: tunnelMode});
    await navigator.clipboard.writeText(url.href);
    field("connection-status").textContent = "Connection link copied.";
  } catch (error) {
    field("connection-status").textContent = error.message;
  }
});

async function display(response) {
  const contentType = response.headers.get("content-type") || "";
  const identifier = response.headers.get("x-request-id");
  statusLine.textContent = `HTTP ${response.status}${identifier ? ` · Request ${identifier}` : ""}`;
  if (response.ok && contentType.startsWith("audio/")) {
    const audio = await response.blob();
    playAudio(audio);
    output.textContent = `${audio.size} bytes · ${audio.type}`;
  } else if (contentType.includes("text/event-stream") && response.body) {
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    const onEvent = typeof receiveSpeechEvent === "function" ? receiveSpeechEvent() : null;
    let buffered = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        output.append(document.createTextNode(value));
        if (onEvent) {
          const events = (buffered + value).split(/\r?\n\r?\n/);
          buffered = events.pop();
          for (const event of events) {
            const data = event.split(/\r?\n/).filter(line => line.startsWith("data:"))
              .map(line => line.slice(5).trimStart()).join("\n");
            if (data) await onEvent(JSON.parse(data));
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  } else {
    const body = await response.text();
    try {
      const decoded = JSON.parse(body);
      output.textContent = JSON.stringify(decoded, null, 2);
      if (response.ok && typeof offerSpeechArtifact === "function") offerSpeechArtifact(decoded);
    } catch {
      output.textContent = body;
    }
  }
}

function toolRequest(config, metadata, headers) {
  const protocol = metadata.mcp.protocolVersion;
  const method = config.tool ? "tools/call" : config.method;
  const params = {
    _meta: {
      "io.modelcontextprotocol/protocolVersion": protocol,
      "io.modelcontextprotocol/clientInfo": { name: "voxrn-console", version: "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": {},
    },
  };
  headers.set("Accept", "application/json, text/event-stream");
  headers.set("Content-Type", "application/json");
  headers.set("MCP-Protocol-Version", protocol);
  headers.set("Mcp-Method", method);
  if (config.tool) {
    headers.set("Mcp-Name", config.tool);
    params.name = config.tool;
    params.arguments = JSON.parse(field("body").value);
  }
  return JSON.stringify({ jsonrpc: "2.0", id: 1, method, params });
}

function httpRequest(config, headers) {
  if (config.idempotent) {
    const key = field("key").value.trim();
    if (!key) throw new Error("Enter an idempotency key.");
    headers.set("Idempotency-Key", key);
  }
  if (config.replay && field("cursor").value.trim()) {
    headers.set("Last-Event-ID", field("cursor").value.trim());
  }
  const format = field("accept").value;
  if (format) headers.set("Accept", format);
  if (config.method === "GET" || config.method === "DELETE") return undefined;
  if (config.media && !field("file").files.length) throw new Error("Choose an audio file.");
  if (config.media === "multipart/form-data") {
    const data = new FormData();
    for (const [key, value] of Object.entries(JSON.parse(field("body").value))) {
      data.append(key, value);
    }
    data.append("file", field("file").files[0]);
    return data;
  }
  if (config.media) {
    const file = field("file").files[0];
    headers.set("Content-Type", file.type || "audio/wav");
    return file;
  }
  if (!config.schema) return undefined;
  headers.set("Content-Type", "application/json");
  return JSON.stringify(JSON.parse(field("body").value));
}

async function modelControlRequest(model, enabled) {
  const path = "/v1/control/models" + (model ? `/${encodeURIComponent(model)}` : "");
  const headers = runtimeHeaders();
  if (model) headers.set("Content-Type", "application/json");
  const response = await fetch(serviceUrl() + path, {
    method: model ? "PUT" : "GET",
    headers,
    body: model ? JSON.stringify({ enabled }) : undefined,
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
  return result;
}

let modelRefreshTimer;
let modelRefreshVersion = 0;
const modelItems = new Map();

const phaseLabels = {
  checking: "Checking system capacity",
  installing_environment: "Preparing isolated environment",
  downloading_weights: "Downloading weights",
  verifying: "Verifying model files",
  starting_runtime: "Starting the inference runtime",
};
const stateLabels = { disabled: "Disabled", ready: "Ready", loading: "Loading", draining: "Draining", failed: "Failed" };
const diskLabels = { not_installed: "Weights not installed", partial: "Partial files on disk",
  installed: "Weights installed", missing: "Installation incomplete" };

function displayBytes(bytes) {
  if (typeof bytes !== "number") return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const power = Math.min(units.length - 1, Math.max(0, Math.floor(Math.log(Math.max(1, bytes)) / Math.log(1024))));
  return numberFormat.format(bytes / 1024 ** power) + " " + units[power];
}

const numberFormat = new Intl.NumberFormat(undefined, {maximumFractionDigits: 1});

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function labeledValues(node, rows) {
  node.replaceChildren(...rows.flatMap(([key, value]) => [element("dt", "", key), element("dd", "", value)]));
}

function healthColor(score) {
  const stops = [[0, 185, 48, 66], [33, 192, 91, 34], [62, 166, 121, 19], [100, 26, 129, 89]];
  const index = stops.findIndex(row => row[0] >= score);
  const high = stops[Math.max(1, index)];
  const low = stops[Math.max(0, index - 1)];
  const mix = (score - low[0]) / (high[0] - low[0]);
  return "rgb(" + low.slice(1).map((value, i) => Math.round(value + (high[i + 1] - value) * mix)).join(",") + ")";
}

function updateModelHealth(item, health) {
  item.measure.hidden = !health;
  if (!health) return;
  item.measureName.textContent = health.label + " · " + health.score;
  item.measureSource.textContent = health.audio_rtf === null || health.audio_rtf === undefined
    ? health.basis : "RTF " + numberFormat.format(health.audio_rtf) + "×";
  item.meter.setAttribute("aria-valuenow", String(health.score));
  item.meter.setAttribute("aria-valuetext", health.label + ", " + health.score + " out of 100");
  item.meter.title = health.detail.join(" · ");
  item.fill.style.width = health.score + "%";
  item.fill.style.backgroundColor = healthColor(health.score);
}

function showModelTest(model) {
  const features = capabilityModels.get(model)?.features;
  if (features?.live && !features.file && document.getElementById("live-asr")) {
    const live = document.getElementById("live-asr");
    live.open = true;
    document.getElementById("live-model").value = model;
    live.scrollIntoView({block: "center"});
    document.getElementById("live-start").focus({preventScroll: true});
    return;
  }
  const operations = {"voxrn-asr": "POST /v1/audio/transcriptions", "voxrn-tts": "POST /v1/audio/speech",
    "voxrn-agent": "POST /v1/chat/completions"};
  field("model").value = model;
  operation.value = operations[runtimeIdentity];
  operation.dispatchEvent(new Event("change"));
  form.scrollIntoView({block: "center"});
  operation.focus({preventScroll: true});
}

function makeModelRow(model, status, list) {
  const item = element("article", "model-row");
  const core = element("div", "model-core");
  const heading = element("h3", "", model);
  const characteristics = element("p", "model-characteristics");
  const storage = element("p", "model-storage");
  core.append(heading, characteristics, storage);
  const measure = element("div", "model-health");
  const captions = element("div", "health-caption");
  const measureName = element("strong", "");
  const measureSource = element("span", "");
  captions.append(measureName, measureSource);
  const meter = element("div", "health-track");
  meter.setAttribute("role", "meter");
  meter.setAttribute("aria-label", model + " resource fit");
  meter.setAttribute("aria-valuemin", "0");
  meter.setAttribute("aria-valuemax", "100");
  const fill = element("span", "health-fill");
  meter.append(fill);
  measure.append(captions, meter);
  core.append(measure);
  const commands = element("div", "model-commands");
  const chip = element("span", "model-status");
  const buttons = element("div", "actions");
  const toggle = element("button", "primary");
  const details = element("button", "", "Details");
  const test = element("button", "", "Test");
  const retry = element("button", "", "Retry");
  for (const control of [toggle, details, test, retry]) control.type = "button";
  const more = element("dl", "model-more");
  const error = element("p", "model-error");
  more.hidden = true;
  details.setAttribute("aria-expanded", "false");
  details.addEventListener("click", () => {
    more.hidden = !more.hidden;
    details.setAttribute("aria-expanded", String(!more.hidden));
  });
  test.addEventListener("click", () => showModelTest(model));
  async function change(enabled) {
    toggle.disabled = retry.disabled = true;
    try {
      await modelControlRequest(model, enabled);
      await refreshModels();
      await fetchCapabilities();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      toggle.disabled = retry.disabled = false;
    }
  }
  toggle.addEventListener("click", () => change(toggle.dataset.enabled !== "true"));
  retry.addEventListener("click", () => change(true));
  buttons.append(details, test, retry, toggle);
  commands.append(chip, buttons);
  const progress = element("div", "model-progress");
  const bar = element("progress", "");
  bar.setAttribute("aria-label", model + " installation progress");
  const progressLabel = element("span", "");
  progress.append(progressLabel, bar);
  item.append(core, commands, progress, error, more);
  list.append(item);
  return {item, characteristics, storage, chip, toggle, test, retry, more, error, progress, bar, progressLabel,
    measure, measureName, measureSource, meter, fill};
}

function updateModelRow(item, row) {
  const {identity, installation: plan} = row;
  const features = capabilityModels.get(identity.model)?.features || {};
  const capabilities = [
    features.live && "Live audio", features.file && "File ASR", features.stream_pcm && "PCM stream",
    features.structured_outputs && "Structured output", features.languages?.length ? features.languages.length + " languages" : "",
  ].filter(Boolean);
  item.characteristics.textContent = [identity.engine, plan?.hardware === "cuda" ? "CUDA" : (plan?.hardware === "cpu" ? "CPU" : ""),
    ...capabilities].filter(Boolean).join(" · ");
  item.storage.textContent = plan ? [
    diskLabels[plan.disk_state] || (plan.installed ? "Weights installed" : "Weights not installed"),
    plan.model_bytes ? displayBytes(plan.model_bytes) + " on disk" : "",
    !plan.installed ? displayBytes(plan.disk_mib * 1024 ** 2) + " required" : "",
  ].filter(Boolean).join(" · ") : (row.active_requests ? row.active_requests + " active" : "");
  item.chip.dataset.state = row.state;
  item.chip.textContent = stateLabels[row.state] || row.state;
  item.toggle.dataset.enabled = String(row.enabled);
  item.toggle.textContent = row.enabled ? "Disable" : "Enable";
  item.toggle.classList.toggle("primary", !row.enabled);
  item.test.hidden = row.state !== "ready";
  item.retry.hidden = row.state !== "failed" || !row.enabled;
  item.error.textContent = row.error_detail || row.error_code || "";
  item.error.hidden = !item.error.textContent;
  const progress = plan?.progress;
  item.progress.hidden = row.state !== "loading";
  item.bar.removeAttribute("value");
  let label = phaseLabels[progress?.phase] || "Loading model";
  if (progress?.total > 0) {
    if (progress.unit === "bytes") {
      item.bar.max = progress.total;
      item.bar.value = progress.completed;
      label += " · " + displayBytes(progress.completed) + " / " + displayBytes(progress.total) + " reported";
    } else {
      label += " · " + progress.completed + " of " + progress.total + " assets verified";
    }
  }
  item.progressLabel.textContent = label;
  const health = runtimeHealth.get(identity.model);
  updateModelHealth(item, health);
  labeledValues(item.more, [
    ["Model revision", identity.model_revision], ["Engine revision", identity.engine_revision],
    ...(plan ? [["Runtime", plan.runtime === "python-venv" ? "Isolated Python environment" : "Ollama"],
      ["Environment", plan.environment_present ? "Present" : "Not installed"],
      ["Host memory budget", displayBytes(plan.memory_mib * 1024 ** 2)],
      ...(plan.vram_mib ? [["GPU memory budget", displayBytes(plan.vram_mib * 1024 ** 2)]] : [])] : []),
    ["Active requests", row.active_requests],
    ...(health ? [
      ...(health.placement ? [
        ["Placement disk", health.placement.source + " mounted at " + health.placement.mount],
        ["Placement space", displayBytes(health.placement.free_bytes) + " free of " + displayBytes(health.placement.total_bytes)],
      ] : []),
      ["Score inputs", health.detail.join(" · ")],
      ["Observed model memory", typeof health.rss_bytes === "number" ? displayBytes(health.rss_bytes) : "Awaiting measurement"],
      ...(runtimeIdentity !== "voxrn-agent" ? [["Audio real-time factor", typeof health.audio_rtf === "number" ? numberFormat.format(health.audio_rtf) + "×" : "Awaiting a completed audio request"]] : []),
    ] : []),
  ]);
}

async function refreshModels() {
  clearTimeout(modelRefreshTimer);
  const version = ++modelRefreshVersion;
  const status = document.getElementById("models-status");
  const list = document.getElementById("models-list");
  try {
    const { models } = await modelControlRequest();
    if (version !== modelRefreshVersion) return;
    const current = new Set();
    for (const row of models) {
      const model = row.identity.model;
      current.add(model);
      if (!modelItems.has(model)) modelItems.set(model, makeModelRow(model, status, list));
      const item = modelItems.get(model);
      item.row = row;
      updateModelRow(item, row);
    }
    for (const [model, {item}] of modelItems) {
      if (!current.has(model)) {
        item.remove();
        modelItems.delete(model);
      }
    }
    status.textContent = models.length ? "" : "The runtime has no model profiles.";
    if (models.some(row => ["loading", "draining"].includes(row.state))) {
      modelRefreshTimer = setTimeout(async () => {
        await refreshModels();
        await fetchCapabilities().catch(() => {});
        if (runtimeIdentity === "voxrn-tts") voices = (await fetchRuntime("/v1/voices").catch(() => ({voices}))).voices;
      }, 750);
    }
  } catch (error) {
    if (version === modelRefreshVersion) status.textContent = error.message;
  }
}

document.addEventListener("voxrn:health", event => {
  runtimeHealth = new Map(Object.entries(event.detail));
  for (const item of modelItems.values()) updateModelRow(item, item.row);
});
document.getElementById("models-refresh").addEventListener("click", refreshModels);

async function loadConsole() {
  const load = async (path) => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`Could not load ${path}`);
    return response.json();
  };
  const [openapi, metadata, examples] = await Promise.all([
    load("contracts/openapi.json"), load("discovery.json"), load("contracts/examples.json"),
  ]);
  runtimeIdentity = metadata.name.toLowerCase().replaceAll(" ", "-");
  const configs = new Map();
  for (const [path, methods] of Object.entries(openapi.paths)) {
    for (const [method, spec] of Object.entries(methods)) {
      if (!spec.responses) continue;
      const input = spec.requestBody?.content || {};
      const outputTypes = Object.keys(spec.responses["200"]?.content || {});
      const media = Object.keys(input).find((item) => item === "multipart/form-data" || item.startsWith("audio/"));
      const name = `${method.toUpperCase()} ${path}`;
      configs.set(name, {
        type: "http", path, method: method.toUpperCase(), media, outputTypes,
        schema: input[media || "application/json"]?.schema,
        idempotent: spec.parameters?.some((param) => param.name === "Idempotency-Key"),
        replay: spec.parameters?.some((param) => param.name === "Last-Event-ID"),
      });
      option(operation, name);
    }
  }
  for (const method of ["server/discover", "tools/list"]) {
    const name = `MCP ${method}`;
    configs.set(name, { type: "mcp", method });
    option(operation, name);
  }
  for (const tool of metadata.mcp.tools) {
    const name = `MCP ${tool.name}`;
    configs.set(name, { type: "mcp", tool: tool.name });
    option(operation, name);
  }
  operation.value = "GET /v1/capabilities";

  function render() {
    const config = configs.get(operation.value);
    const http = config.type === "http";
    field("id-label").hidden = !(http && config.path.includes("{"));
    field("file-label").hidden = !config.media;
    if (field("mic-actions")) field("mic-actions").hidden = !config.media;
    field("key-label").hidden = !config.idempotent;
    field("cursor-label").hidden = !config.replay;
    if (config.idempotent) field("key").value = crypto.randomUUID();
    field("accept-label").hidden = !http || !config.outputTypes.length;
    field("body-label").hidden = !config.tool && !config.schema;
    field("body").value = JSON.stringify(initialBody(config, examples), null, 2);
    field("accept").replaceChildren();
    for (const format of config.outputTypes || []) option(field("accept"), format);
    if (tunnelMode === "quick" && config.outputTypes?.includes("application/json")) {
      field("accept").value = "application/json";
    }
    selectRequestModel();
  }
  operation.addEventListener("change", render);
  document.querySelectorAll("[data-operation]").forEach(link => {
    link.addEventListener("click", () => {
      if (configs.has(link.dataset.operation)) {
        operation.value = link.dataset.operation;
        render();
      }
    });
  });
  field("stop").addEventListener("click", () => request?.abort());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (request) return;
    clearResponse();
    const config = configs.get(operation.value);
    const headers = runtimeHeaders();
    request = new AbortController();
    field("run").disabled = true;
    field("stop").disabled = false;
    const timeout = setTimeout(() => request?.abort("deadline"), 150000);
    try {
      const mcp = config.type === "mcp";
      const id = field("id").value.trim();
      const path = mcp ? metadata.mcp.path : config.path.replace(/\{[^}]+\}/g, encodeURIComponent(id));
      if (!mcp && config.path.includes("{") && !id) throw new Error("Enter a run or media ID.");
      const url = serviceUrl() + path;
      const body = mcp ? toolRequest(config, metadata, headers) : httpRequest(config, headers);
      const jsonStream = !mcp && path === "/v1/chat/completions" && body && JSON.parse(body).stream;
      if (tunnelMode === "quick" && !mcp && (field("accept").value === "text/event-stream" || jsonStream)) {
        throw new Error("Use a named Cloudflare tunnel to test server-sent events.");
      }
      statusLine.textContent = "Waiting for the runtime…";
      await display(await fetch(url, {
        method: mcp ? "POST" : config.method, headers, body, signal: request.signal, redirect: "error", cache: "no-store",
      }));
    } catch (error) {
      statusLine.textContent = request.signal.aborted
        ? (request.signal.reason === "deadline" ? "Request timed out." : "Request cancelled.")
        : `Request failed. ${error.message}`;
    } finally {
      clearTimeout(timeout);
      request = null;
      field("run").disabled = false;
      field("stop").disabled = true;
    }
  });
  async function hydrateLink() {
    const session = window.voxrnConnection;
    delete window.voxrnConnection;
    const linkedEndpoint = session?.endpoint || field("url").value;
    if (linkedEndpoint && session?.key) {
      field("url").value = linkedEndpoint;
      field("token").value = session.key;
      tunnelMode = session.transport || (new URL(linkedEndpoint).hostname.endsWith(".trycloudflare.com") ? "quick" : "named");
      await connect();
    }
    render();
  }

  window.addEventListener("voxrn:connection", () => {
    hydrateLink().catch(error => { statusLine.textContent = error.message; });
  });
  await hydrateLink();
  statusLine.textContent = "Ready";
}

if (location.pathname === "/docs/" || location.pathname === "/docs/index.html") {
  field("url").value = location.origin;
}
field("run").disabled = true;
loadConsole().then(() => { field("run").disabled = false; }).catch((error) => {
  statusLine.textContent = error.message;
});
