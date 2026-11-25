"use strict";
const el = id => document.getElementById(id);
let model = null, enabled = false, busy = false;

function render() {
  const count = model ? Object.keys(model.probabilities).length.toLocaleString("zh-CN") : "";
  const field = model?.field === "b_initial" ? "词频和考纲" : "仅词频";
  el("status").textContent = model ? `${enabled ? "试用已开启" : "试用已暂停"} · ${field} · ${count} 个词元` : "当前使用原有判词规则。";
  el("toggle").hidden = el("remove").hidden = !model;
  el("toggle").textContent = enabled ? "暂停试用" : "重新启用";
  for (const id of ["table","parameters","field","apply","toggle","remove"]) el(id).disabled = busy;
}

async function refresh() {
  const stored = await chrome.storage.local.get(["irtPreview","irtEnabled"]);
  model = AdaptiveWordIRT.validPreview(stored.irtPreview) ? stored.irtPreview : null;
  enabled = !!model && stored.irtEnabled === true;
  render();
}

async function change(action) {
  if (busy) return;
  busy = true; render(); el("error").textContent = "";
  try { await action(); await refresh(); }
  catch (error) { el("error").textContent = error.message || "操作未完成，请重试。"; }
  finally { busy = false; render(); }
}

el("import").onsubmit = event => {
  event.preventDefault();
  const tableFile = el("table").files[0], paramsFile = el("parameters").files[0], field = el("field").value;
  change(async () => {
    const bytes = await tableFile.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    const decode = new TextDecoder("utf-8", { fatal: true });
    const table = JSON.parse(decode.decode(bytes));
    const params = JSON.parse(decode.decode(await paramsFile.arrayBuffer()));
    const next = AdaptiveWordIRT.compile(table, params, hash, field);
    await chrome.storage.local.set({ irtPreview: next, irtEnabled: true });
    el("import").reset();
  });
};
el("toggle").onclick = () => change(() => chrome.storage.local.set({ irtEnabled: !enabled }));
el("remove").onclick = () => change(() => chrome.storage.local.remove(["irtPreview","irtEnabled"]));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.irtPreview || changes.irtEnabled)) refresh().catch(e => el("error").textContent = e.message);
});
refresh().catch(e => el("error").textContent = e.message);
