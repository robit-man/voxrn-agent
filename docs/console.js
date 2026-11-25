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
  const headers = new Headers();
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
    document.getElementById("model-control").open = true;
    await refreshModels();
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
  const headers = new Headers();
  const token = field("token").value.trim();
  if (token) headers.set("Authorization", `Bearer ${token}`);
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
      if (!modelItems.has(model)) {
        const item = document.createElement("li");
        const label = document.createTextNode("");
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.addEventListener("click", async () => {
          toggle.disabled = true;
          try {
            await modelControlRequest(model, toggle.dataset.enabled !== "true");
            await refreshModels();
            await fetchCapabilities();
          } catch (error) {
            status.textContent = error.message;
          } finally {
            toggle.disabled = false;
          }
        });
        item.append(label, toggle);
        list.append(item);
        modelItems.set(model, {item, label, toggle});
      }
      const {label, toggle} = modelItems.get(model);
      const plan = row.installation;
      const install = plan && !plan.installed ? ` · download on enable (${plan.disk_mib} MiB disk)` : "";
      const hardware = plan?.hardware === "cuda" ? " · requires CUDA" : "";
      const failure = row.error_detail || row.error_code || "";
      label.textContent = `${model} · ${row.state} · ${row.active_requests} active${install}${hardware} ${failure} `;
      toggle.dataset.enabled = String(row.enabled);
      toggle.textContent = row.enabled ? "Disable" : "Enable";
    }
    for (const [model, {item}] of modelItems) {
      if (!current.has(model)) {
        item.remove();
        modelItems.delete(model);
      }
    }
    status.textContent = models.length ? "" : "No installed model profiles.";
    if (models.some(row => ["loading", "draining"].includes(row.state))) {
      modelRefreshTimer = setTimeout(async () => {
        await refreshModels();
        await fetchCapabilities().catch(() => {});
      }, 750);
    }
  } catch (error) {
    if (version === modelRefreshVersion) status.textContent = error.message;
  }
}

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
  field("stop").addEventListener("click", () => request?.abort());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (request) return;
    clearResponse();
    const config = configs.get(operation.value);
    const token = field("token").value.trim();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
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
