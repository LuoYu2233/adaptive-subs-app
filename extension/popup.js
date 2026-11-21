"use strict";
const LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000];
const DEFAULTS = { enabled: true, level: 3000, pref: "more", mode: "adaptive", gloss: "over", hideNative: true, pos: "bottom", cover: "on" };
const $ = (s) => document.querySelector(s);
let cfg = { ...DEFAULTS };
let prof = null;          // profile summary from the local program (null: program not running / old version)

const nearest = (x) => LEVELS.reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
const level = () => (prof ? nearest(prof.level) : cfg.level);

function render() {
  $("#enabled").checked = cfg.enabled;
  $("#hideNative").checked = cfg.hideNative;
  const i = Math.max(0, LEVELS.indexOf(level()));
  $("#level").value = i;
  $("#levelValue").textContent = `≈ ${LEVELS[i].toLocaleString("en-US")} 词`;
  $("#levelNote").textContent = !prof ? "程序没开时只保存在这个浏览器里" : prof.test
    ? `来自词汇测试（${prof.test.date}）· 拖动可以手动调整`
    : prof.setup ? "按考试成绩 / 身份估计 · 做一次词汇测试会更准" : "拖动调整，或建立英语画像自动设置";
  document.querySelectorAll(".seg").forEach((seg) => {
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(cfg[seg.dataset.key] === b.dataset.v)));
  });
  document.querySelector("main").style.opacity = cfg.enabled ? "" : "0.45";
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

// UP 主s whose cover board was closed with × on the video: offer to bring it back for all of them
function renderCoverOff(cover) {
  const off = Object.keys(cover || {}).filter((k) => cover[k] && cover[k].off);
  const el = $("#coverOff");
  el.hidden = !off.length;
  if (!off.length) return;
  el.innerHTML = `已对 ${off.length} 个 UP 主关掉了遮挡板 · <a href="#" id="coverRestore">全部恢复</a>`;
  $("#coverRestore").onclick = (e) => {
    e.preventDefault();
    const next = { ...cover };
    off.forEach((k) => delete next[k]);
    chrome.storage.local.set({ asubsCover: next });
    renderCoverOff(next);
  };
}

chrome.storage.local.get(["settings", "asubsCover"], (r) => {
  renderCoverOff(r && r.asubsCover);
  cfg = { ...DEFAULTS, ...((r && r.settings) || {}) };
  render();
});

$("#enabled").addEventListener("change", (e) => { cfg.enabled = e.target.checked; save(); });
$("#hideNative").addEventListener("change", (e) => { cfg.hideNative = e.target.checked; save(); });
$("#level").addEventListener("input", (e) => {
  cfg.level = LEVELS[+e.target.value];
  save();
  if (prof) {
    prof.level = cfg.level;
    clearTimeout(window._lvTimer);           // save to the shared profile once the slider stops
    window._lvTimer = setTimeout(() => {
      chrome.runtime.sendMessage({ type: "setprofile", payload: { level: cfg.level } }, () => {
        chrome.storage.local.set({ profileRev: Date.now() });     // tell open Bilibili tabs to reload it
      });
    }, 300);
  }
  render();
});
document.querySelectorAll(".seg").forEach((seg) => {
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { cfg[seg.dataset.key] = b.dataset.v; save(); }
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
  } else if (!r.key) {
    status("bad", `已连接，但还没填 ${r.provider || "注释模型"} 的 API key：点右下角「程序设置」`);
  } else {
    status("ok", `已连接 · 注释用 ${r.provider}` + (r.asr ? " · 可以语音识别" : " · 语音识别未设置"));
  }
});
