"use strict";
const LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000];
const DEFAULTS = { enabled: true, level: 3000, pref: "more", mode: "adaptive", gloss: "over", hideNative: true, pos: "bottom", cover: "on" };
const $ = (s) => document.querySelector(s);
let cfg = { ...DEFAULTS };
let prof = null;          // profile summary from the local program (null: program not running / old version)

const nearest = (x) => LEVELS.reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
let draftLevel = null, levelRequest = 0;
const level = () => draftLevel ?? (prof ? prof.level : cfg.level);

function render() {
  $("#enabled").checked = cfg.enabled;
  $("#hideNative").checked = cfg.hideNative;
  const i = Math.max(0, LEVELS.indexOf(nearest(level())));
  $("#level").value = i;
  $("#levelValue").textContent = `≈ ${level().toLocaleString("en-US")} 词`;
  $("#levelNote").textContent = !prof ? "程序没开时只保存在这个浏览器里" : prof.test
    ? `来自词汇测试（${prof.test.date}）· 拖动可以手动调整`
    : prof.setup ? "按考试成绩 / 身份估计 · 做一次词汇测试会更准" : "拖动调整，或建立英语画像自动设置";
  document.querySelectorAll(".seg[data-key]").forEach((seg) => {
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(cfg[seg.dataset.key] === b.dataset.v)));
  });
  document.querySelector("main").style.opacity = cfg.enabled ? "" : "0.45";
  $("#coverHere").classList.toggle("off", cfg.cover === "off");
}

function renderProfile() {
  const box = $("#profileBox");
  if (!prof) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle("todo", !prof.setup);
  $("#profileText").innerHTML = !prof.setup
    ? "<b>还没有建立英语画像。</b>填一下考试成绩、做个 3 分钟的词汇测试，注释会准很多。"
    : `熟词库 <b>${prof.stats.known.toLocaleString("en-US")}</b> 词${prof.lists.length ? ` · 含${prof.lists.join("、")}词表` : ""}` +
      ` · 标记过 <b>${prof.stats.marked_known + prof.stats.marked_unknown}</b> 个词`;
  $("#openProfile").textContent = prof.setup ? "英语画像 ›" : "建立英语画像（约 3 分钟）";
}

function save() {
  chrome.storage.local.set({ settings: cfg });
  render();
}

// (The cover board is off by default for every UP 主 / channel — P on the video or the row
// below turns it on — so there is no list of UP 主s it was closed for any more.)
chrome.storage.local.get(["settings"], (r) => {
  cfg = { ...DEFAULTS, ...((r && r.settings) || {}) };
  render();
});

$("#enabled").addEventListener("change", (e) => { cfg.enabled = e.target.checked; save(); });
$("#hideNative").addEventListener("change", (e) => { cfg.hideNative = e.target.checked; save(); });
$("#level").addEventListener("input", (e) => {
  draftLevel = LEVELS[+e.target.value];
  render();
});
$("#level").addEventListener("change", () => {
  cfg.level = draftLevel ?? LEVELS[+$("#level").value];
  save();
  if (!prof) { draftLevel = null; $("#saveStatus").textContent = "已保存在此浏览器；本机画像尚未连接"; return; }
  const request = ++levelRequest;
  $("#saveStatus").textContent = "正在保存词汇量…";
  // Send on release/change, before the popup can close. The service worker owns ordering.
  chrome.runtime.sendMessage({ type: "setprofile", payload: { level: cfg.level } }, (result) => {
    const error = chrome.runtime.lastError;
    if (request !== levelRequest) return;
    draftLevel = null;
    if (error || !result || result.error || !Number.isFinite(result.level)) {
      $("#saveStatus").textContent = `画像未保存：${result?.error || "连接失败，请重试"}`;
      render();
      return;
    }
    prof = result; renderProfile(); render();
    $("#saveStatus").textContent = "已保存到本机画像";
    chrome.storage.local.set({ profileRev: result.rev });
  });
});
document.querySelectorAll(".seg[data-key]").forEach((seg) => {
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { cfg[seg.dataset.key] = b.dataset.v; save(); }
  });
});

// the video in the current tab: cover board on / off for its UP 主 / YouTube channel
// (the tab's content script answers; on other pages nobody does and the row stays hidden)
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const tab = tabs && tabs[0];
  if (!tab || tab.id == null) return;
  chrome.tabs.sendMessage(tab.id, { type: "where" }, (w) => {
    if (chrome.runtime.lastError || !w || !w.owner) return;
    const row = $("#coverHere"), seg = row.querySelector(".seg");
    const name = w.name.length > 14 ? `${w.name.slice(0, 13)}…` : w.name;
    $("#coverHereLabel").textContent = `${w.here}${name ? `（${name}）` : ""}`;
    const show = (on) => seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String((b.dataset.v === "on") === on)));
    show(w.cover);
    row.hidden = false;
    seg.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      chrome.tabs.sendMessage(tab.id, { type: "cover", on: b.dataset.v === "on" }, (r) => {
        if (chrome.runtime.lastError || !r) return;
        show(r.cover);
      });
    });
  });
});

function status(kind, text) {
  const el = $("#status");
  el.className = `chip ${kind}`;
  el.querySelector("span").textContent = text;
}

chrome.runtime.sendMessage({ type: "health" }, (r) => {
  const btn = $("#openSetup");
  if (r && r.setup) {
    btn.hidden = false;
    btn.onclick = () => chrome.tabs.create({ url: r.setup });
  }
  if (r && r.profile) {
    $("#openProfile").onclick = () => chrome.tabs.create({ url: r.profile });
    chrome.runtime.sendMessage({ type: "profile", summary: true }, (p) => {
      if (chrome.runtime.lastError || !p || p.error) return;
      prof = p;
      renderProfile();
      render();
    });
  }
  if (chrome.runtime.lastError || !r || r.error === "noserver") {
    status("bad", "Adaptive Subs 程序没开：打开 AdaptiveSubs.exe 后刷新视频页。现在只能看纯英文字幕");
  } else if (!r.ext) {
    status("bad", "程序是旧版本：关掉它的窗口重新打开");
  } else if (r.versionMismatch) {
    status("bad", `已连接程序 ${r.version}，与插件版本不同：请退出正在运行的程序，使用同一版本包内的程序和插件，再刷新视频页`);
  } else if (!r.key) {
    status("bad", `已连接，但还没填 ${r.provider || "注释模型"} 的 API key：点右下角「程序设置」`);
  } else {
    status("ok", `已连接 · 注释用 ${r.provider}` + (r.asr ? " · 可以语音识别" : " · 语音识别未设置"));
  }
});
