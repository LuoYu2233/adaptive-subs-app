"use strict";
const LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000];
const DEFAULTS = { enabled: true, level: 3000, pref: "more", mode: "adaptive", gloss: "over", hideNative: true, pos: "bottom" };
const $ = (s) => document.querySelector(s);
let cfg = { ...DEFAULTS };

function render() {
  $("#enabled").checked = cfg.enabled;
  $("#hideNative").checked = cfg.hideNative;
  const i = Math.max(0, LEVELS.indexOf(cfg.level));
  $("#level").value = i;
  $("#levelValue").textContent = `≈ ${LEVELS[i].toLocaleString("en-US")} 词`;
  document.querySelectorAll(".seg").forEach((seg) => {
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(cfg[seg.dataset.key] === b.dataset.v)));
  });
  document.querySelector("main").style.opacity = cfg.enabled ? "" : "0.5";
}

function save() {
  chrome.storage.local.set({ settings: cfg });
  render();
}

chrome.storage.local.get("settings", (r) => {
  cfg = { ...DEFAULTS, ...((r && r.settings) || {}) };
  render();
});

$("#enabled").addEventListener("change", (e) => { cfg.enabled = e.target.checked; save(); });
$("#hideNative").addEventListener("change", (e) => { cfg.hideNative = e.target.checked; save(); });
$("#level").addEventListener("input", (e) => { cfg.level = LEVELS[+e.target.value]; save(); });
document.querySelectorAll(".seg").forEach((seg) => {
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { cfg[seg.dataset.key] = b.dataset.v; save(); }
  });
});

chrome.runtime.sendMessage({ type: "health" }, (r) => {
  const el = $("#status"), btn = $("#openSetup");
  if (r && r.setup) {
    btn.hidden = false;
    btn.onclick = () => chrome.tabs.create({ url: r.setup });
  }
  if (chrome.runtime.lastError || !r || r.error === "noserver") {
    el.className = "status bad";
    el.textContent = "Adaptive Subs 程序没开：先运行 AdaptiveSubs.exe（或 start.bat）。不开也能看纯英文字幕，但没有注释";
  } else if (!r.ext) {
    el.className = "status bad";
    el.textContent = "Adaptive Subs 程序是旧版本：关掉它的窗口重新打开";
  } else if (!r.key) {
    el.className = "status bad";
    el.textContent = `程序已连接，但还没填 ${r.provider || "注释模型"} 的 API key：点下面「打开设置」`;
  } else {
    el.className = "status ok";
    el.textContent = `已连接（注释：${r.provider}）。` +
      (r.asr ? "没有英文字幕的视频可以用语音识别。" : "语音识别还没填小米 MiMo 的 key，只能看有英文字幕的视频。");
  }
});
