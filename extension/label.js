"use strict";
const byId = id => document.getElementById(id);
const tell = text => { byId("notice").textContent = text; };
const tabId = Number(new URLSearchParams(location.search).get("tab"));
let port, source, key, draft, index = 0, chosen = [], phrase = false, anchor = null;
let busy = false, connected = false, oldDraft = false, releaseLock;

function wordLists(p) {
  return p && [p.known, p.unknown].every(words => Array.isArray(words) && words.every(w => typeof w === "string"));
}

function validProfile(p) {
  return Number.isFinite(p?.level) && p.level > 0 && wordLists(p) && (p.marks === undefined || wordLists(p.marks));
}

function validDraft(value) {
  const sheet = value?.sheet;
  if (!validProfile(value?.profile) || sheet?.version !== 1 || sheet.video !== source.video ||
      sheet.duration !== source.duration || sheet.cues?.length !== source.cues.length ||
      !Number.isInteger(value.index) || value.index < 0 || value.index >= source.cues.length) return false;
  return source.cues.every(({ text, s, e }, i) => {
    const row = sheet.cues[i];
    if (row?.s !== s || row.e !== e || row.text !== text) return false;
    if (row.unknown === null) return true;
    if (!Array.isArray(row.unknown)) return false;
    const points = [...text];
    let end = 0;
    for (const mark of row.unknown) {
      if (!Number.isInteger(mark?.s) || mark.s < end || typeof mark.text !== "string" || !mark.text.trim()) return false;
      end = mark.s + [...mark.text].length;
      if (points.slice(mark.s, end).join("") !== mark.text) return false;
    }
    return true;
  });
}

function controls() {
  for (const id of ["new", "resume", "previous", "replay", "confirm", "phrase", "selection", "clear"]) {
    byId(id).disabled = busy || !connected || (id === "previous" && index === 0);
  }
  for (const button of byId("sentence").querySelectorAll("button")) button.disabled = busy || !connected;
}

function addSpan(s, e) {
  if (busy || !connected) return;
  const text = [...source.cues[index].text].slice(s, e).join("");
  const existing = chosen.findIndex(mark => mark.s === s && mark.text === text);
  if (existing >= 0) chosen.splice(existing, 1);
  else {
    if (chosen.some(mark => s < mark.s + [...mark.text].length && mark.s < e)) {
      render(); tell("与已选范围重叠，请先点下方已选词撤销。"); return;
    }
    chosen.push({ s, text });
    chosen.sort((a, b) => a.s - b.s);
  }
  tell("本句更改将在确认后保存。");
  render();
}

function pick(s, e) {
  if (!phrase) return addSpan(s, e);
  if (!anchor) { anchor = { s, e }; render(); return; }
  const start = Math.min(anchor.s, s), end = Math.max(anchor.e, e);
  anchor = null; phrase = false;
  addSpan(start, end);
}

function render() {
  const cue = draft.sheet.cues[index];
  const confirmed = draft.sheet.cues.filter(c => Array.isArray(c.unknown)).length;
  byId("progress").textContent = `第 ${index + 1} / ${draft.sheet.cues.length} 句`;
  byId("saved").textContent = `已确认 ${confirmed} 句。导出会包含所有已确认的答案。`;
  byId("confirm").textContent = index + 1 === source.cues.length ? "确认最后一句" : "确认并播放下句";
  byId("phrase").setAttribute("aria-pressed", String(phrase));
  byId("hint").textContent = phrase ? (anchor ? "请选择短语的末词。" : "请选择短语的首词。")
    : "点词可标记或撤销。短语可以点首尾两个词，也可拖选原文后标记。";
  const line = byId("sentence");
  const focused = line.contains(document.activeElement) ? document.activeElement.dataset.start : undefined;
  line.replaceChildren();
  let cursor = 0, offset = 0;
  for (const match of cue.text.matchAll(/[\p{L}\p{N}]+(?:['’‐‑-][\p{L}\p{N}]+)*/gu)) {
    const gap = cue.text.slice(cursor, match.index);
    line.append(gap);
    const s = offset + [...gap].length, e = s + [...match[0]].length;
    const token = document.createElement("button");
    token.textContent = match[0];
    token.dataset.start = String(s);
    token.setAttribute("aria-pressed", String(chosen.some(mark => mark.s <= s && e <= mark.s + [...mark.text].length)));
    if (anchor?.s === s) token.className = "anchor";
    token.onclick = () => { if (document.getSelection()?.isCollapsed !== false) pick(s, e); };
    line.append(token);
    cursor = match.index + match[0].length;
    offset = e;
  }
  line.append(cue.text.slice(cursor));
  if (focused !== undefined) line.querySelector(`[data-start="${focused}"]`)?.focus();
  const selected = byId("selected");
  selected.replaceChildren();
  for (const mark of chosen) {
    const button = document.createElement("button");
    button.textContent = mark.text + " ×";
    button.setAttribute("aria-label", "撤销标记 " + mark.text);
    button.onclick = () => addSpan(mark.s, mark.s + [...mark.text].length);
    button.disabled = busy || !connected;
    selected.append(button);
  }
  controls();
}

function go(to, play) {
  index = to;
  chosen = [...(draft.sheet.cues[index].unknown || [])];
  phrase = false; anchor = null;
  render();
  if (connected) port.postMessage({ type: "cue", index, play });
}

async function writeDraft(next) {
  await chrome.storage.local.set({ [key]: next });
  draft = next;
}

async function action(fn) {
  if (busy || !connected) return;
  busy = true; controls();
  try { await fn(); }
  catch (error) { tell("操作未完成：" + error.message); }
  finally {
    busy = false;
    if (draft && !byId("editor").hidden) render(); else controls();
    if (!connected) releaseLock?.();
  }
}

function enter() {
  byId("start").hidden = true;
  byId("editor").hidden = false;
  tell("");
  go(draft.index, true);
}

byId("new").onclick = () => action(async () => {
  if (oldDraft && !window.confirm("开始新标注将替换这段视频在本浏览器中的旧草稿。继续吗？")) return;
  const p = await chrome.runtime.sendMessage({ type: "profile" });
  if (!validProfile(p)) throw new Error("无法读取观看前画像，请确认本地程序已运行后重试。");
  const profile = { level: p.level, known: p.known, unknown: p.unknown };
  if (p.marks) profile.marks = { known: p.marks.known, unknown: p.marks.unknown };
  await writeDraft({ sheet: structuredClone(source), profile, index: 0 });
  enter();
});
byId("resume").onclick = () => { if (connected && !busy) enter(); };
byId("replay").onclick = () => port.postMessage({ type: "cue", index, play: true });
byId("phrase").onclick = () => { phrase = !phrase; anchor = null; render(); };
byId("selection").onmousedown = event => event.preventDefault();
byId("selection").onclick = () => {
  const selection = document.getSelection(), line = byId("sentence");
  if (!selection.rangeCount || selection.isCollapsed) return tell("请先在英文原句中拖选要标记的文字。");
  const range = selection.getRangeAt(0);
  if (!line.contains(range.startContainer) || !line.contains(range.endContainer)) return tell("请只选本句英文。");
  const prefix = range.cloneRange();
  prefix.selectNodeContents(line); prefix.setEnd(range.startContainer, range.startOffset);
  const raw = range.toString(), text = raw.trim();
  if (!text) return tell("请选择包含文字的范围。");
  const s = [...prefix.toString()].length + [...raw.slice(0, raw.indexOf(text))].length;
  selection.removeAllRanges();
  phrase = false; anchor = null;
  addSpan(s, s + [...text].length);
};
byId("previous").onclick = () => {
  if (index === 0) return;
  if (JSON.stringify(chosen) !== JSON.stringify(draft.sheet.cues[index].unknown || []) &&
      !window.confirm("放弃本句尚未确认的更改？")) return;
  go(index - 1, false);
};
byId("confirm").onclick = () => action(async () => {
  if (anchor) return tell("请先选完短语或关闭「选择短语」。");
  const next = structuredClone(draft);
  next.sheet.cues[index].unknown = chosen;
  next.index = Math.min(index + 1, next.sheet.cues.length - 1);
  await writeDraft(next);
  const last = next.index === index;
  go(next.index, !last);
  tell(last ? "已保存，可导出标注和观看前画像。" : "");
});

byId("clear").onclick = () => action(async () => {
  if (!window.confirm("清除此视频的标注和观看前画像？已下载的文件会保留。")) return;
  await chrome.storage.local.remove(key);
  draft = null; oldDraft = false;
  byId("editor").hidden = true; byId("resume").hidden = true;
  byId("draftStatus").textContent = ""; byId("start").hidden = false;
  tell("已清除此视频的本地草稿。");
});

function download(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
byId("answers").onclick = () => download(draft.sheet.video + "-labels.json", draft.sheet);
byId("viewer").onclick = () => download(draft.sheet.video + "-viewer-before.json", draft.profile);

function disconnected() {
  connected = false; controls();
  if (!busy) releaseLock?.();
  if (draft && !byId("editor").hidden) render();
  if (!byId("notice").textContent) tell("视频页已断开。已确认的答案可继续导出。");
}

async function openDraft(sheet, title) {
  source = sheet; key = "blindLabels:" + sheet.video;
  byId("title").textContent = title;
  await navigator.locks.request(key, { ifAvailable: true }, async lock => {
    if (!lock) { tell("此视频已在另一个窗口标注，请使用原窗口。"); port.disconnect(); disconnected(); return; }
    try {
      const result = await chrome.storage.local.get(key);
      if (!connected) return;
      oldDraft = Object.hasOwn(result, key);
      if (validDraft(result[key])) {
        draft = result[key];
        byId("resume").hidden = false;
        byId("draftStatus").textContent = "已找到与当前字幕吻合的草稿。";
      } else if (oldDraft) {
        byId("draftStatus").textContent = "现有草稿和当前字幕不匹配。开始新标注会替换旧草稿。";
      }
      byId("start").hidden = false; tell(""); controls();
      await new Promise(resolve => {
        releaseLock = resolve;
        window.addEventListener("pagehide", resolve, { once: true });
      });
      releaseLock = undefined;
    } catch (error) { tell("读取本地草稿失败：" + error.message); port.disconnect(); disconnected(); }
  });
}

if (Number.isSafeInteger(tabId) && tabId > 0) {
  port = chrome.tabs.connect(tabId, { name: "blind-labels" });
  connected = true;
  let stopReason = "";
  port.onMessage.addListener(message => {
    if (message.type === "sheet") openDraft(message.sheet, message.title).catch(error => tell(error.message));
    if (message.type === "notice") tell(message.text);
    if (message.type === "stopped") { stopReason = message.reason; tell(stopReason); disconnected(); }
  });
  port.onDisconnect.addListener(() => {
    const failure = chrome.runtime.lastError;
    tell(stopReason || (failure ? "无法连接视频页，请刷新原视频页后重试。" : "视频页已断开，已确认的答案可导出。"));
    disconnected();
  });
} else tell("请从视频页的插件弹窗打开逐句盲标。");
