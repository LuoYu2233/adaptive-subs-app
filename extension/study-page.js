"use strict";
const el = id => document.getElementById(id);
const tab = Number(new URLSearchParams(location.search).get("tab"));
let draft = null, ready = false, busy = false, refreshing = 0, retryTimer;
const openedAt = Date.now();
async function message(msg) {
  const res = await chrome.runtime.sendMessage(msg);
  if (!res || res.error) throw new Error(res?.error || "无法读取本机记录");
  return res;
}
async function toVideo(msg) {
  try { return await chrome.tabs.sendMessage(tab, msg); }
  catch { return { error: "请在已加载完整字幕的视频页打开扩展，再进入观看记录。" }; }
}
function render() {
  el("retry").hidden = !!draft || ready;
  el("form").hidden = !!draft || !ready;
  el("stop").hidden = !draft?.recording;
  el("export").hidden = el("clear").hidden = !draft || draft.recording;
  for (const b of document.querySelectorAll("button")) b.disabled = busy;
  el("status").textContent = draft ? `${draft.clip} · ${draft.domain} · ${draft.count} 次单词出现 · ${draft.recording ? "正在记录" :
    draft.reason === "capacity" ? "已达到 5000 次上限" : "已停止"}` :
    ready ? "当前视频可开始记录。" : "请在已启用 IRT 试用、已载入完整字幕的视频上打开此页。";
}
async function refresh() {
  const call = ++refreshing;
  clearTimeout(retryTimer);
  const next = (await message({ type: "studyStatus" })).draft;
  const available = !next && !!(await toVideo({ type: "studyReady" }))?.ready;
  if (call !== refreshing) return;
  draft = next; ready = available; render();
  if (!next && !available && tab > 0 && Date.now() - openedAt < 15000)
    retryTimer = setTimeout(() => refresh().catch(e => el("error").textContent = e.message), 900);
}
async function action(fn) {
  if (busy) return;
  busy = true; render(); el("error").textContent = "";
  try { await fn(); await refresh(); }
  catch (e) { el("error").textContent = e.message; }
  finally { busy = false; render(); }
}
el("retry").onclick = () => action(refresh);
window.addEventListener("focus", () => refresh().catch(e => el("error").textContent = e.message));
el("form").onsubmit = e => {
  e.preventDefault();
  action(async () => {
    const r = await toVideo({ type: "studyStart", clip: el("clip").value.trim(), domain: el("domain").value.trim() });
    if (!r?.id) throw new Error(r?.error || "当前页无法开始记录");
  });
};
el("stop").onclick = () => action(() => message({ type: "studyStop", id: draft.id }));
el("export").onclick = () => action(async () => {
  const data = await message({ type: "studyExport", id: draft.id });
  const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url; a.download = `观看记录-${draft.clip}-${draft.id}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
});
el("clear").onclick = () => {
  if (confirm("清除此组本机记录？已下载的文件仍然保留。")) action(() => message({ type: "studyClear", id: draft.id }));
};
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.wordStudyDraft) refresh().catch(e => el("error").textContent = e.message);
});
refresh().catch(e => el("error").textContent = e.message);
