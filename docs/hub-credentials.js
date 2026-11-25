"use strict";

(() => {
  const form = document.getElementById("hub-key-form");
  const field = document.getElementById("hub-key");
  const save = document.getElementById("hub-key-save");
  const remove = document.getElementById("hub-key-remove");
  const status = document.getElementById("hub-key-status");
  let pending;
  let configured = false;
  let manageable = false;

  function updateControls() {
    field.disabled = !manageable || !!pending;
    save.disabled = field.disabled || !field.value.trim();
    remove.disabled = field.disabled || !configured;
    save.textContent = configured ? "Replace key" : "Save key";
  }

  function disconnect() {
    pending?.abort();
    pending = null;
    field.value = "";
    configured = false;
    manageable = false;
    status.textContent = "Connect with model management access.";
    updateControls();
  }

  async function change(method, token) {
    if (pending || !workbenchConnected() || (method !== "GET" && !manageable)) return;
    const headers = runtimeHeaders();
    const controller = new AbortController();
    pending = controller;
    let body;
    if (method === "PUT") {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify({token});
      field.value = "";
    }
    updateControls();
    try {
      const response = await fetch(serviceUrl() + "/v1/control/huggingface", {
        method, headers, body, redirect: "error", cache: "no-store",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      });
      if (pending !== controller) return;
      if (response.status === 401 || response.status === 403) {
        manageable = false;
        status.textContent = "Connect with model management access.";
        return;
      }
      if (!response.ok) {
        status.textContent = response.status === 400
          ? "Enter a Hugging Face access token."
          : "Hugging Face key storage is unavailable.";
        return;
      }
      const result = await response.json();
      if (pending !== controller) return;
      if (typeof result.configured !== "boolean") throw new Error("Invalid key status.");
      configured = result.configured;
      manageable = true;
      status.textContent = configured ? "Key saved on this runtime." : "No key saved.";
    } catch {
      if (pending === controller) status.textContent = "Hugging Face key storage is unavailable.";
    } finally {
      if (pending === controller) {
        pending = null;
        updateControls();
      }
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    const token = field.value.trim();
    if (token) change("PUT", token);
  });
  field.addEventListener("input", updateControls);
  remove.addEventListener("click", () => change("DELETE"));
  document.addEventListener("voxrn:connected", () => change("GET"));
  document.addEventListener("voxrn:disconnected", disconnect);
  disconnect();
})();
