/* Adaptive Subs extension — runs inside www.bilibili.com/video/… pages.
   1. Reads the video's English subtitle track from Bilibili's API. The request is made from the page,
      so it carries the viewer's own login (Bilibili only lists subtitles to logged-in viewers).
   2. Shows the English lines over Bilibili's own player right away, synced to the <video> element.
   3. Sends the lines to the local Adaptive Subs server (via background.js) and adds Chinese glosses
      as they arrive — only for words above the viewer's vocabulary level (same rules as the player). */
(() => {
  "use strict";
  if (window.__adaptiveSubs) return;
  window.__adaptiveSubs = true;

  const LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000];
  const PREFS = {
    more: { factor: 0.7, perLine: 4, repeat: 3 },
    normal: { factor: 1.0, perLine: 3, repeat: 2 },
    less: { factor: 1.3, perLine: 2, repeat: 1 },
  };
  const DEFAULTS = { enabled: true, level: 3000, pref: "more", mode: "adaptive", gloss: "over", hideNative: true, pos: "bottom" };
  const GAP_GRACE = 0.5;     // a line stays this long after it ends, then the box disappears
  // Bilibili request signature ("WBI"): fixed shuffle of the two keys published by /nav
  const MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28,
    14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52];

  let cfg = { ...DEFAULTS };
  const S = {
    key: null,        // "BV…/p" of the page the subtitles belong to
    gen: 0,           // bumped on every new video, so stale async work can tell it's outdated
    id: null,         // server job id
    cues: [],         // [{s, e, text, zh, notes:[{s, e, zh, lv, kind, key}]}]
    visible: [],      // per cue: Set of note indices to gloss
    annDone: null,    // while generating: Set of lines whose glosses arrived
    since: 0,         // events read from the server
    cur: -2, dirty: true,
    lastT: 0, lastFocusAt: 0,
  };
  const ui = { root: null, box: null, line: null, zh: null, pill: null, host: null, ro: null, pillTimer: 0 };

  // ------------------------------------------------------------ helpers ---
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const bg = (msg) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => resolve(chrome.runtime.lastError ? { error: "ext" } : (r || { error: "ext" })));
    } catch {
      resolve({ error: "ext" });   // the extension was reloaded: this copy of the script is orphaned
    }
  });
  const SERVER_MSG = {
    noserver: "Adaptive Subs 程序没开：先运行它（AdaptiveSubs.exe 或 start.bat），再刷新本页（现在先显示纯英文字幕）",
    oldserver: "Adaptive Subs 程序是旧版本：关掉它的窗口重新打开，再刷新本页",
    ext: "插件刚被重新加载：请刷新本页",
  };
  const serverMsg = (r) => SERVER_MSG[r.error] || r.error || "出错了";

  // --------------------------------------------------------- Bilibili API ---
  async function biliJSON(path) {
    const r = await fetch("https://api.bilibili.com" + path, { credentials: "include" });
    const j = await r.json();
    if (j.code !== 0) throw new Error(`B 站接口返回 ${j.code}：${j.message || ""}`);
    return j.data;
  }

  let wbi = null;
  async function wbiInfo() {
    if (wbi && Date.now() - wbi.at < 3600e3) return wbi;
    // answers with code -101 when logged out, but the signing keys are there either way
    const j = await (await fetch("https://api.bilibili.com/x/web-interface/nav", { credentials: "include" })).json();
    const name = (u) => u.slice(u.lastIndexOf("/") + 1).split(".")[0];
    const raw = name(j.data.wbi_img.img_url) + name(j.data.wbi_img.sub_url);
    wbi = { mixin: MIXIN.map((n) => raw[n]).join("").slice(0, 32), loggedIn: !!j.data.isLogin, at: Date.now() };
    return wbi;
  }
  function signed(params, mixin) {
    const p = { ...params, wts: Math.round(Date.now() / 1000) };
    const q = Object.keys(p).sort()
      .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(String(p[k]).replace(/[!'()*]/g, ""))}`).join("&");
    return `${q}&w_rid=${md5(q + mixin)}`;
  }

  function pageKey() {
    const m = location.pathname.match(/\/video\/(BV[0-9A-Za-z]{10})/) || location.search.match(/[?&]bvid=(BV[0-9A-Za-z]{10})/);
    if (!m) return null;
    const p = parseInt(new URLSearchParams(location.search).get("p") || "1", 10) || 1;
    return { bvid: m[1], p, str: `${m[1]}/${p}` };
  }

  // English only; the uploader's own subtitles before Bilibili's AI ones; no Chinese-English mixes
  function pickEnglish(subs) {
    const en = subs.filter((s) => (/^(ai-)?en\b/i.test(s.lan || "") || /英/.test(s.lan_doc || "")) && !/中|汉|双语/.test(s.lan_doc || ""));
    en.sort((a, b) => Number(/^ai-/i.test(a.lan)) - Number(/^ai-/i.test(b.lan)));
    return en[0] || null;
  }

  // ------------------------------------------------------------- loading ---
  async function load(key) {
    const gen = ++S.gen;
    Object.assign(S, { key: key.str, id: null, asr: false, cues: [], visible: [], annDone: null, since: 0, cur: -2, dirty: true });
    if (!cfg.enabled) return;
    pill("正在读取 B 站字幕…");
    try {
      const view = await biliJSON(`/x/web-interface/view?bvid=${key.bvid}`);
      const pages = view.pages || [];
      const page = pages.find((x) => x.page === key.p) || pages[0] || { cid: view.cid, page: 1, part: "" };
      const title = pages.length > 1 ? `${view.title} · P${page.page} ${page.part}` : view.title;
      const w = await wbiInfo();
      let info;
      try {
        info = await biliJSON(`/x/player/wbi/v2?${signed({ bvid: key.bvid, cid: page.cid }, w.mixin)}`);
      } catch {
        info = await biliJSON(`/x/player/v2?bvid=${key.bvid}&cid=${page.cid}`);
      }
      if (gen !== S.gen) return;
      const subs = (info.subtitle && info.subtitle.subtitles) || [];
      const track = pickEnglish(subs);
      const url = track && (track.subtitle_url || track.subtitle_url_v2);
      if (!track || !url) {
        const why = !subs.length
          ? (info.need_login_subtitle && !w.loggedIn ? "没登录 B 站，拿不到字幕（登录后可能有英文字幕）" : "这个视频没有字幕文件")
          : !track ? `这个视频没有英文字幕（只有：${subs.map((s) => s.lan_doc).join("、")}）` : "拿不到字幕文件地址";
        return offerASR(gen, key, page, view, title, why);
      }

      const sub = await bg({ type: "subtitle", url: new URL(url, "https://www.bilibili.com").href.replace(/^http:/, "https:") });
      if (gen !== S.gen) return;
      if (sub.error) return pill(sub.error === "ext" ? SERVER_MSG.ext : sub.error, { sticky: true });
      const cues = ((sub.data && sub.data.body) || [])
        .map((b) => ({ s: +b.from, e: +b.to, text: String(b.content || "").replace(/\s*\n\s*/g, " ").trim() }))
        .filter((c) => c.text && c.e > c.s);
      if (!cues.length) return pill("字幕文件是空的", { sticky: true });
      setCues(cues);                                    // plain English right away
      const ai = /^ai-/i.test(track.lan);
      pill(`已载入${ai ? " B 站 AI 生成的" : "UP 主上传的"}英文字幕，正在连接本地服务器…`);

      S.id = `bili_${key.bvid}_${page.cid}_${track.lan}`.replace(/[^A-Za-z0-9_.-]/g, "_");
      const r = await bg({ type: "open", payload: { id: S.id, title, duration: page.duration || view.duration || 0, source: ai ? "auto" : "manual", cues } });
      if (gen !== S.gen) return;
      if (r.error) return pill(serverMsg(r), { sticky: true });
      if (r.cached) {
        setCues(r.data.cues);
        return pill("注释已载入（这个视频之前生成过）", { hideAfter: 4000 });
      }
      S.annDone = new Set();
      pill("正在生成注释，先处理你正在看的位置…");
      poll(gen);
    } catch (e) {
      if (gen === S.gen) pill(`读取字幕失败：${e.message || e}`, { sticky: true });
    }
  }

  // ------------------------------------------------- speech recognition ---
  // Videos without English subtitles: the local server downloads the audio and recognises the speech.
  // Started by a click (it costs a little, and most videos without English subtitles are in Chinese),
  // unless it was done before — then the result just loads.
  async function offerASR(gen, key, page, view, title, why) {
    const payload = { id: `bili_${key.bvid}_${page.cid}_asr`, title, duration: page.duration || view.duration || 0,
      source: "asr", bvid: key.bvid, p: page.page || key.p };
    const probe = await bg({ type: "open", payload: { ...payload, probe: true } });
    if (gen !== S.gen) return;
    if (probe.cached) {
      S.id = payload.id;
      setCues(probe.data.cues);
      return pill("已载入之前语音识别的英文字幕和注释", { hideAfter: 4000 });
    }
    if (probe.running) return follow(gen, payload.id, "语音识别进行中…");
    if (probe.error && probe.error !== "noasr") return pill(`${why}。${serverMsg(probe)}`, { sticky: true });
    if (probe.error === "noasr") return pill(`${why}。本地服务器没有配置语音识别（.env 里的 MIMO_API_KEY）`, { sticky: true });
    pill(`${why}。视频说的是英语的话，可以用语音识别生成英文字幕（10 分钟约 ¥0.1）`, {
      sticky: true,
      action: { label: "语音识别英文字幕", fn: () => startASR(gen, payload) },
    });
  }

  async function startASR(gen, payload) {
    if (gen !== S.gen) return;
    pill("正在启动语音识别…", { sticky: true });
    const r = await bg({ type: "open", payload });
    if (gen !== S.gen) return;
    if (r.error) return pill(serverMsg(r), { sticky: true });
    if (r.cached) { S.id = payload.id; setCues(r.data.cues); return pill("已载入", { hideAfter: 3000 }); }
    follow(gen, payload.id, "正在下载音频…");
  }

  function follow(gen, id, msg) {
    S.id = id;
    S.since = 0;
    S.annDone = new Set();
    pill(msg);
    poll(gen);
  }

  function setCues(cues) {
    S.cues = cues.map((c) => ({ s: c.s, e: c.e, text: c.text, zh: c.zh || "", notes: c.notes || [] }));
    computeVisible();
  }

  async function poll(gen) {
    let errors = 0;
    while (gen === S.gen) {
      const r = await bg({ type: "events", id: S.id, since: S.since });
      if (gen !== S.gen) return;
      if (r.error || !Array.isArray(r.events)) {
        if (r.error === "no such job") return pill("本地服务器重启过：刷新本页重新生成", { sticky: true });
        if (++errors >= 5) return pill(serverMsg(r), { sticky: true });
        await sleep(2000);
        continue;
      }
      errors = 0;
      S.since = r.next;
      let finished = false;
      for (const [type, d] of r.events) {
        if (type === "meta") {                // the server's cleaned-up lines: indices refer to these
          S.asr = d.source === "asr";
          const old = S.cues;
          setCues(d.cues.map((c, i) => (old[i] && old[i].text === c.text ? { ...c, zh: old[i].zh, notes: old[i].notes } : c)));
        } else if (type === "ann") {
          for (const [k, notes] of Object.entries(d)) if (S.cues[+k]) { S.cues[+k].notes = notes; S.annDone && S.annDone.add(+k); }
        } else if (type === "zh") {
          for (const [k, z] of Object.entries(d)) if (S.cues[+k]) S.cues[+k].zh = z;
        } else if (type === "text") {           // speech recognition: a line's words arrived
          for (const [k, t] of Object.entries(d)) if (S.cues[+k]) S.cues[+k].text = t;
          S.dirty = true;
        } else if (type === "progress") {
          const pct = Math.round((100 * d.done) / Math.max(1, d.total));
          pill(`${S.cues.length && S.asr ? "语音识别 + " : ""}生成注释 ${pct}% · 先处理你正在看的位置`);
        } else if (type === "done") {
          finished = true;
          S.annDone = null;
          pill(`注释已全部生成（用时 ${d.seconds} 秒），下次打开会直接载入`, { hideAfter: 5000 });
        } else if (type === "fail") {
          finished = true;
          pill(d.msg, { sticky: true });
        } else if (type === "status") {
          pill(d.msg);
        }
      }
      computeVisible();
      if (finished) return;
    }
  }

  // ------------------------------------------- which glosses to show (core) ---
  function computeVisible() {
    const pref = PREFS[cfg.pref] || PREFS.more;
    const count = new Map();
    S.visible = S.cues.map((c) => {
      const cand = c.notes.map((n, i) => ({ i, lv: n.lv, key: n.key })).filter((x) => x.lv > cfg.level * pref.factor);
      cand.sort((a, b) => b.lv - a.lv);
      const keep = new Set();
      for (const x of cand) {
        if (keep.size >= pref.perLine) break;
        const seen = count.get(x.key) || 0;
        if (seen >= pref.repeat) continue;
        count.set(x.key, seen + 1);
        keep.add(x.i);
      }
      return keep;
    });
    S.dirty = true;
  }

  // ------------------------------------------------------------ overlay ---
  function findVideo() {
    return document.querySelector(".bpx-player-video-wrap video, .bpx-player-video-wrap bwp-video, #bilibili-player video");
  }

  function ensureOverlay(video) {
    const host = video.closest(".bpx-player-video-area") || video.closest(".bpx-player-container") || video.parentElement;
    if (ui.root && ui.host === host && host.contains(ui.root)) return true;
    if (ui.ro) ui.ro.disconnect();
    if (ui.root) ui.root.remove();
    ui.host = host;
    if (getComputedStyle(host).position === "static") host.style.position = "relative";
    ui.root = el("div", "asubs-root");
    ui.box = el("div", "asubs-box");
    ui.line = el("div", "asubs-line");
    ui.zh = el("div", "asubs-zh");
    ui.box.append(ui.line, ui.zh);
    ui.pill = el("div", "asubs-pill");
    ui.pill.hidden = true;
    ui.root.append(ui.box, ui.pill);
    host.append(ui.root);
    ui.ro = new ResizeObserver(() => {     // subtitle size follows the player (also in fullscreen)
      const w = host.clientWidth || 800;
      ui.root.style.setProperty("--asubs-size", `${Math.max(16, Math.min(46, w * 0.027)).toFixed(1)}px`);
    });
    ui.ro.observe(host);
    S.dirty = true;
    if (pillState.text) showPill();
    return true;
  }

  const pillState = { text: "", sticky: false, hideAfter: 0, action: null };
  function pill(text, opts = {}) {
    Object.assign(pillState, { text, sticky: !!opts.sticky, hideAfter: opts.hideAfter || 0, action: opts.action || null });
    showPill();
  }
  function showPill() {
    if (!ui.pill) return;
    clearTimeout(ui.pillTimer);
    ui.pill.replaceChildren(pillState.text ? `Adaptive Subs · ${pillState.text}` : "");
    if (pillState.text && pillState.action) {
      const b = el("button", "asubs-pill-btn", pillState.action.label);
      b.type = "button";
      const act = pillState.action.fn;
      // keep the click away from the player underneath (it would pause / play the video)
      for (const ev of ["pointerdown", "mousedown", "mouseup", "dblclick"]) b.addEventListener(ev, (e) => e.stopPropagation());
      b.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); b.disabled = true; act(); });
      ui.pill.append(b);
    }
    ui.pill.hidden = !pillState.text || !cfg.enabled;
    ui.pill.classList.toggle("asubs-pill-sticky", pillState.sticky);
    if (pillState.hideAfter) ui.pillTimer = setTimeout(() => { ui.pill.hidden = true; pillState.text = ""; }, pillState.hideAfter);
  }

  function renderLine(i) {
    ui.root.classList.toggle("asubs-top", cfg.pos === "top");
    const show = cfg.enabled && i >= 0 && !!S.cues[i].text;
    ui.box.hidden = !show;
    if (!show) return;
    const c = S.cues[i], vis = S.visible[i] || new Set();
    const style = cfg.mode === "adaptive" ? cfg.gloss : "none";
    ui.line.className = "asubs-line" + (style === "paren" ? " asubs-paren" : "") + (style === "over" && vis.size ? " asubs-has-over" : "");
    ui.line.replaceChildren();
    let pos = 0;
    c.notes.forEach((n, k) => {
      if (n.s < pos || n.e > c.text.length) return;
      ui.line.append(c.text.slice(pos, n.s));
      const word = c.text.slice(n.s, n.e);
      if (style !== "none" && vis.has(k)) {
        const g = el("span", style === "over" ? "asubs-g" : "asubs-p");
        g.append(el("span", "asubs-w", word), el("span", style === "over" ? "asubs-gz" : "asubs-pz", style === "over" ? n.zh : `(${n.zh})`));
        ui.line.append(g);
      } else {
        ui.line.append(word);
      }
      pos = n.e;
    });
    ui.line.append(c.text.slice(pos));
    const zh = cfg.mode === "bilingual" ? c.zh : "";
    ui.zh.textContent = zh;
    ui.zh.hidden = !zh;
  }

  function cueAt(t) {      // last line starting at or before t (binary search)
    let lo = 0, hi = S.cues.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (S.cues[mid].s <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  function tick() {
    const k = pageKey();
    if ((k && k.str) !== S.key) {           // Bilibili changes video / part without reloading the page
      if (k) load(k); else { S.key = null; S.gen++; S.cues = []; }
    }
    document.documentElement.classList.toggle("asubs-hide-native", cfg.enabled && cfg.hideNative && S.cues.length > 0);
    const video = findVideo();
    if (!video) return;
    ensureOverlay(video);
    const t = video.currentTime || 0;
    const i = S.cues.length ? cueAt(t) : -1;
    const live = i >= 0 && t <= S.cues[i].e + GAP_GRACE ? i : -1;   // silences: nothing on screen
    if (live !== S.cur || S.dirty) {
      S.cur = live; S.dirty = false;
      renderLine(live);
    }
    // while glosses are being generated, tell the server where the viewer is (after a jump, or every 20 s)
    if (S.annDone && S.id) {
      const jumped = Math.abs(t - S.lastT) > 3;
      if (jumped || Date.now() - S.lastFocusAt > 20000) {
        S.lastFocusAt = Date.now();
        bg({ type: "focus", id: S.id, t: t.toFixed(1) });
      }
    }
    S.lastT = t;
  }

  // ------------------------------------------------------------ settings ---
  chrome.storage.local.get("settings", (r) => {
    cfg = { ...DEFAULTS, ...((r && r.settings) || {}) };
    if (!LEVELS.includes(cfg.level)) cfg.level = DEFAULTS.level;
    computeVisible();
    setInterval(tick, 100);
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.settings) return;
    const wasOn = cfg.enabled;
    cfg = { ...DEFAULTS, ...(changes.settings.newValue || {}) };
    computeVisible();
    showPill();
    if (cfg.enabled && !wasOn) { S.key = null; }      // turned back on: load this video's subtitles
  });
})();
