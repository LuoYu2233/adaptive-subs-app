"use strict";
const { LEVELS, DEFAULTS } = AdaptiveSettings;
const $ = (s) => document.querySelector(s);
let cfg = { ...DEFAULTS };
let prof = null;
let profileState = "connecting", profileError = "", profileRead = 0;

function validProfile(p) {
  const count = n => Number.isSafeInteger(n) && n >= 0;
  return p && !p.error && count(p.rev) && Number.isInteger(p.level) && p.level >= 1 && p.level <= 100000
    && typeof p.setup === "boolean" && p.stats && ["known", "marked_known", "marked_unknown"].every(k => count(p.stats[k]))
    && Array.isArray(p.lists) && p.lists.length <= 20 && p.lists.every(s => typeof s === "string" && s.length <= 80)
    && (p.test == null || (typeof p.test === "object" && !Array.isArray(p.test)
      && (p.test.date == null || (typeof p.test.date === "string" && p.test.date.length <= 80))));
}

const nearest = (x) => LEVELS.reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
let draftLevel = null, levelRequest = 0, savingLevel = false;
const level = () => draftLevel ?? (prof ? prof.level : cfg.level);

function render() {
  $("#enabled").checked = cfg.enabled;
  $("#hideNative").checked = cfg.hideNative;
  const i = Math.max(0, LEVELS.indexOf(nearest(level())));
  $("#level").value = i;
  const editable = profileState === "ready" || profileState === "offline";
  $("#level").disabled = !editable || savingLevel;
  $("#levelValue").textContent = editable ? `≈ ${level().toLocaleString("en-US")} 词`
    : profileState === "unavailable" ? "暂不可用" : "读取中…";
  $("#levelNote").textContent = profileState === "connecting" ? "正在确认本机画像连接…"
    : profileState === "loading" ? "正在读取本机画像，请稍候…"
    : profileState === "unavailable" ? "已连接程序，但画像尚未读取；恢复后才能修改词汇量。"
    : profileState === "offline" ? "未连接本机画像，词汇量只保存在这个浏览器里" : prof.test
    ? `来自词汇测试${prof.test.date ? `（${prof.test.date}）` : ""} · 拖动可以手动调整`
    : prof.setup ? "按考试成绩 / 身份估计 · 做一次词汇测试会更准" : "拖动调整，或建立英语画像自动设置";
  document.querySelectorAll(".seg[data-key]").forEach((seg) => {
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(cfg[seg.dataset.key] === b.dataset.v)));
  });
  document.querySelector("main").style.opacity = cfg.enabled ? "" : "0.45";
  $("#coverHere").classList.toggle("off", cfg.cover === "off");
}

function renderProfile() {
  const box = $("#profileBox");
  box.classList.toggle("unavailable", profileState === "unavailable");
  $("#retryProfile").hidden = profileState !== "unavailable";
  if (profileState === "unavailable") {
    box.hidden = false;
    box.classList.remove("todo");
    $("#profileText").textContent = profileError;
    $("#openProfile").textContent = "打开画像页面 ›";
    return;
  }
  if (!prof) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle("todo", !prof.setup);
  $("#profileText").textContent = !prof.setup
    ? "还没有建立英语画像。填一下考试成绩、做个 3 分钟的词汇测试，注释会准很多。"
    : `熟词库 ${prof.stats.known.toLocaleString("en-US")} 词${prof.lists.length ? ` · 含${prof.lists.join("、")}词表` : ""}` +
      ` · 标记过 ${prof.stats.marked_known + prof.stats.marked_unknown} 个词`;
  $("#openProfile").textContent = prof.setup ? "英语画像 ›" : "建立英语画像（约 3 分钟）";
}

function loadProfile() {
  const request = ++profileRead;
  profileState = "loading";
  prof = null;
  draftLevel = null;
  $("#saveStatus").textContent = "";
  renderProfile(); render();
  chrome.runtime.sendMessage({ type: "profile", summary: true }, p => {
    const failed = chrome.runtime.lastError;
    if (request !== profileRead) return;
    if (failed || !validProfile(p)) {
      profileState = "unavailable";
      const detail = typeof p?.error === "string" && p.error !== "noserver" ? p.error : "请求失败或响应不完整";
      profileError = `画像暂时无法读取：${detail}。可以重试，或打开画像页面查看原因。`;
    } else {
      prof = p;
      profileState = "ready";
    }
    renderProfile(); render();
  });
}

$("#retryProfile").addEventListener("click", loadProfile);

let settingsWrite = 0;
function save(onStored) {
  const request = ++settingsWrite;
  chrome.storage.local.set({ settings: cfg }, () => {
    const failed = chrome.runtime.lastError;
    if (request !== settingsWrite) return;
    $("#settingsIssue").hidden = !failed;
    if (failed) $("#settingsIssue").textContent = "显示设置未能保存，当前修改仅在本次弹窗中生效。可以重新选择设置后重试。";
    if (onStored) onStored(!failed);
  });
  render();
}

// (The cover board is off by default for every UP 主 / channel — P on the video or the row
// below turns it on — so there is no list of UP 主s it was closed for any more.)
chrome.storage.local.get(["settings"], (r) => {
  const failed = chrome.runtime.lastError;
  const checked = AdaptiveSettings.inspect(r?.settings);
  cfg = checked.settings;
  $("#settingsIssue").hidden = !failed && !checked.invalid;
  $("#settingsIssue").textContent = failed ? "显示设置暂时无法读取，当前使用默认显示；已有画像和标记不变。"
    : "部分显示设置无效，当前使用对应默认值；原始存储尚未修改，已有画像和标记不变。";
  render();
});

$("#enabled").addEventListener("change", (e) => { cfg.enabled = e.target.checked; save(); });
$("#hideNative").addEventListener("change", (e) => { cfg.hideNative = e.target.checked; save(); });
$("#level").addEventListener("input", (e) => {
  if (savingLevel || (profileState !== "ready" && profileState !== "offline")) return;
  draftLevel = LEVELS[+e.target.value];
  render();
});
$("#level").addEventListener("change", () => {
  if (savingLevel || (profileState !== "ready" && profileState !== "offline")) return;
  const requestedLevel = draftLevel ?? LEVELS[+$("#level").value];
  if (!prof) {
    cfg.level = requestedLevel;
    $("#saveStatus").textContent = "正在保存浏览器设置…";
    save(ok => { $("#saveStatus").textContent = ok ? "已保存在此浏览器；本机画像尚未连接" : "浏览器设置未保存，请重试"; });
    draftLevel = null;
    return;
  }
  const request = ++levelRequest;
  const previousRev = prof.rev;
  savingLevel = true;
  render();
  $("#saveStatus").textContent = "正在保存词汇量…";
  const finish = (p, message, persistFallback) => {
    if (request !== levelRequest) return;
    savingLevel = false;
    draftLevel = null;
    prof = p;
    if (p) {
      profileState = "ready";
      // A best-effort notification; clients also refresh profiles periodically.
      chrome.storage.local.set({ profileRev: p.rev }, () => { void chrome.runtime.lastError; });
    } else {
      profileState = "unavailable";
      profileError = "保存结果尚未确认，画像也暂时无法读取。请重新读取或打开画像页面核对，程序不会自动重发这次修改。";
    }
    if (persistFallback) { cfg.level = requestedLevel; save(); }
    renderProfile(); render();
    $("#saveStatus").textContent = message;
  };
  // Send on release/change, before the popup can close. The service worker owns ordering.
  chrome.runtime.sendMessage({ type: "setprofile", payload: { level: requestedLevel } }, (result) => {
    const error = chrome.runtime.lastError;
    if (request !== levelRequest) return;
    if (!error && validProfile(result) && result.rev >= previousRev && result.level === requestedLevel) {
      finish(result, "已保存到本机画像", true);
      return;
    }
    $("#saveStatus").textContent = "保存响应未确认，正在重新读取画像核对…";
    // A lost response may follow a successful write. Read once, without replaying the mutation.
    chrome.runtime.sendMessage({ type: "profile", summary: true }, current => {
      const failed = chrome.runtime.lastError;
      if (request !== levelRequest) return;
      if (failed || !validProfile(current) || current.rev < previousRev) {
        finish(null, "保存结果尚未确认，请恢复画像连接后核对", false);
        return;
      }
      const matches = current.level === requestedLevel;
      finish(current, matches ? "已重新读取：当前词汇量与本次设置一致"
        : "保存结果未确认；已重新读取当前词汇量，请核对后再调整", matches);
    });
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
  const failed = chrome.runtime.lastError;
  const btn = $("#openSetup");
  if (r && r.setup) {
    btn.hidden = false;
    btn.onclick = () => chrome.tabs.create({ url: r.setup });
  }
  if (r && r.profile) {
    $("#openProfile").onclick = () => chrome.tabs.create({ url: r.profile });
    loadProfile();
  } else {
    profileState = "offline";
    render();
  }
  if (failed || !r || r.error === "noserver") {
    status("bad", "Adaptive Subs 程序没开：打开 AdaptiveSubs.exe 后刷新视频页。现在只能看纯英文字幕");
  } else if (!r.ext) {
    status("bad", "程序是旧版本：关掉它的窗口重新打开");
  } else if (r.versionMismatch) {
    status("bad", `已连接程序 ${r.version}，与插件版本不同：请退出正在运行的程序，使用同一版本包内的程序和插件，再刷新视频页`);
  } else if (r.runtime && r.runtime.state !== "ready") {
    status("bad", r.runtime.message || "本地英语组件还未就绪，请查看控制台");
    btn.hidden = false;
    btn.textContent = "打开控制台";
    btn.onclick = () => chrome.tabs.create({url: r.base + "/app"});
  } else if (!r.key) {
    status("bad", `已连接，但还没填 ${r.provider || "注释模型"} 的 API key：点右下角「程序设置」`);
  } else {
    status("ok", `已连接 · 注释用 ${r.provider}` + (r.asr ? " · 可以语音识别" : " · 语音识别未设置"));
  }
});
