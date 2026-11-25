"use strict";

const catalog = document.getElementById("ollama-catalog");
const catalogStatus = document.getElementById("ollama-status");
const catalogRows = document.getElementById("ollama-models");
const catalogFilter = document.getElementById("ollama-filter");
const catalogRefresh = document.getElementById("ollama-refresh");
let inventory = null;
let catalogRequest = 0;
let catalogConnected = false;

function renderCatalog() {
  if (!inventory) return;
  const query = catalogFilter.value.trim().toLowerCase();
  const visible = inventory.models.filter(model => (model.tag + " " + model.digest).toLowerCase().includes(query));
  if (!visible.length) {
    catalogRows.replaceChildren(element("p", "small-status", query ? "No matching installed models." : "The provider has no installed models."));
    return;
  }
  catalogRows.replaceChildren(...visible.map(model => {
    const row = element("article", "model-row");
    const content = element("div", "model-core");
    const revision = model.digest.replace(/^sha256:/, "");
    content.append(element("h3", "", model.tag),
      element("p", "model-storage", displayBytes(model.size_bytes) + " · " + revision.slice(0, 12)));
    if (model.registered_model) content.append(element("p", "model-characteristics", model.registered_model));
    const button = element("button", "catalog-action", model.registered_model ? "Registered" : "Add to library");
    button.type = "button";
    button.disabled = !!model.registered_model;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const headers = runtimeHeaders();
        headers.set("Content-Type", "application/json");
        const response = await fetch(serviceUrl() + "/v1/control/ollama/registrations", {
          method: "POST", headers, redirect: "error", cache: "no-store", signal: AbortSignal.timeout(10000),
          body: JSON.stringify({tag: model.tag, digest: model.digest}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "HTTP " + response.status);
        await refreshModels();
        await refreshCatalog();
      } catch (error) {
        catalogStatus.textContent = error.message;
        button.disabled = false;
      }
    });
    row.append(content, button);
    return row;
  }));
}

async function refreshCatalog() {
  if (!catalogConnected) {
    catalogStatus.textContent = "Connect to a runtime to inspect its Ollama inventory.";
    return;
  }
  const version = ++catalogRequest;
  catalogRefresh.disabled = true;
  catalogStatus.textContent = "Reading installed Ollama models…";
  try {
    const data = await fetchRuntime("/v1/control/ollama/catalog");
    if (version !== catalogRequest) return;
    inventory = data;
    document.getElementById("ollama-count").textContent = data.models.length + " installed";
    catalogStatus.textContent = "Ollama " + data.provider.engine_revision + " · " + data.provider.url;
    renderCatalog();
  } catch (error) {
    if (version === catalogRequest) {
      inventory = null;
      catalogRows.replaceChildren();
      catalogStatus.textContent = "Catalog unavailable. " + error.message;
    }
  } finally {
    if (version === catalogRequest) catalogRefresh.disabled = false;
  }
}

document.addEventListener("voxrn:connected", () => {
  catalogConnected = true;
  refreshCatalog();
});
catalog.addEventListener("toggle", () => {
  if (catalog.open && !inventory) refreshCatalog();
});
catalogRefresh.addEventListener("click", refreshCatalog);
catalogFilter.addEventListener("input", renderCatalog);
