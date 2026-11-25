/* Adaptive Subs extension — runs on Bilibili video pages (www.bilibili.com/video/…) and YouTube watch pages.
   1. Gets the video's English subtitles:
      - Bilibili: from Bilibili's API. The request is made from the page, so it carries the viewer's own
        login (Bilibili only lists subtitles to logged-in viewers). Shown right away, before any glosses.
      - YouTube: the local server fetches them (YouTube's caption files only answer its own player).
   2. Shows the English lines over the site's own player, synced to the <video> element.
   3. Adds Chinese glosses from the local Adaptive Subs server (via background.js) as they are generated —
      only for words above the viewer's vocabulary level (same rules as the player).
   Videos without English subtitles: the server can recognise the speech (started by a click). */
(() => {
  "use strict";
  if (window.__adaptiveSubs) return;
  window.__adaptiveSubs = true;

  const LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000];
  const PREFS = AdaptivePrediction.preferences;
  const DEFAULTS = { enabled: true, level: 3000, pref: "more", mode: "adaptive", gloss: "over", hideNative: true, pos: "bottom", cover: "on" };
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
  const ui = { root: null, box: null, line: null, zh: null, pill: null, host: null, ro: null, pillTimer: 0, card: null, cover: null,
               toast: null, toastTimer: 0 };
  // Z: this line's Chinese while the key is held; a quick tap keeps it until the next line starts
  const PEEK = { hold: false, sticky: false, line: -1, downAt: 0, wasSticky: false };
  const MODES = ["adaptive", "plain", "bilingual"];          // B cycles through them (same setting as the popup)
  const MODE_NAMES = { adaptive: "自适应（只注释生词）", plain: "纯英文", bilingual: "中英对照" };
  let keysHint = true;      // false until the one-time shortcut hint has been shown (chrome.storage: asubsKeysHint)

  // What differs between the two sites: where the player is, and whose videos the cover board and the
  // subtitle position are remembered for (UP 主 / channel). Where the subtitles come from: load().
  const YOUTUBE = /(^|\.)youtube\.com$/.test(location.hostname);
  const SITE = YOUTUBE ? {
    here: "这个频道",
    video: "#movie_player video.html5-main-video, #movie_player video",
    host: "#movie_player",
    controls: ".ytp-chrome-bottom",
    coverByDefault: false,      // few YouTube videos have subtitles printed in the picture: off until asked for
  } : {
    here: "这个 UP 主",
    video: ".bpx-player-video-wrap video, .bpx-player-video-wrap bwp-video, #bilibili-player video",
    host: ".bpx-player-video-area, .bpx-player-container",
    controls: ".bpx-player-control-wrap",
    coverByDefault: false,      // off until the viewer turns it on for an UP 主 (P, or the popup)
  };

  // Learner profile from the local program (shared with the profile page and the player):
  //   known   = known-word library (exam lists + frequent words + test + "我认识" marks)
  //   unknown = always gloss ("我不认识" marks + words answered 不认识 in the test)
  // Phrase and special-sense notes use their own keys ("phrase:…", "sense:…"), so knowing a word
  // never hides its uncommon meaning.
  const PF = { ok: false, rev: -1, level: 0, known: new Set(), unknown: new Set(), marks: new Map(), lastCheck: 0 };
  let defs = {};    // key -> short Chinese meaning, for words marked 不认识 that had no annotation
  const markKey = AdaptivePrediction.markKey;
  const effLevel = () => (PF.ok && PF.level) || cfg.level;

  // ------------------------------------------------------------ helpers ---
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const WORD_RE = /[A-Za-z](?:[A-Za-z'’-]*[A-Za-z])?/g;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  // element with children (strings become text): no HTML strings — YouTube enforces Trusted Types,
  // where assigning innerHTML throws
  const h = (tag, cls, ...kids) => {
    const e = el(tag, cls);
    for (const k of kids) if (k != null && k !== false && k !== "") e.append(k);
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
    noserver: "Adaptive Subs 程序没开：先运行它（AdaptiveSubs.exe 或 start.bat），再刷新本页" + (YOUTUBE ? "" : "（现在先显示纯英文字幕）"),
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
    if (YOUTUBE) {
      const v = location.pathname === "/watch" ? new URLSearchParams(location.search).get("v")
        : (location.pathname.match(/^\/live\/([A-Za-z0-9_-]{11})/) || [])[1];
      return v && /^[A-Za-z0-9_-]{11}$/.test(v) ? { v, str: `yt:${v}` } : null;
    }
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
    Object.assign(S, { key: key.str, page: key, id: null, savedVideo: null, savedCues: null, finished: false, asr: false, cues: [], visible: [], annDone: null, since: 0, cur: -2, dirty: true,
      owner: null, ownerName: "" });
    lastLayout = "";
    if (!cfg.enabled) return;
    return YOUTUBE ? loadYouTube(gen, key) : loadBili(gen, key);
  }

  // YouTube: the local server fetches the English subtitles (manual ones first, else YouTube's automatic
  // ones) and generates the glosses; the lines arrive with its first event
  async function loadYouTube(gen, key) {
    pill("正在获取 YouTube 英文字幕…");
    const r = await bg({ type: "open", payload: { id: key.v, source: "youtube", subs_only: true } });
    if (gen !== S.gen) return;
    if (r.cached) {
      S.id = r.id;
      setSaved(r.data);
      return pill(r.id === key.v ? "注释已载入（这个视频之前生成过）" : "已载入之前语音识别的英文字幕和注释", { hideAfter: 4000 });
    }
    if (r.nosubs && S.page) return offerASR(gen, ytASR(S.page), "这个视频没有英文字幕");
    if (r.error) return pill(serverMsg(r), r.retry ? { sticky: true, action: { label: "重试", fn: () => { S.key = null; } } } : { sticky: true });
    // started before (another tab), or an older program that starts generating right away
    if (r.running || !r.cues) return follow(gen, r.id || key.v, "正在获取 YouTube 英文字幕…");
    setCues(r.cues);                               // plain English now; glosses after a click
    offerGloss(gen, { id: key.v, source: "youtube" }, `已载入 YouTube ${r.source === "auto" ? "自动生成的" : "上传者提供的"}英文字幕`);
  }

  // Glosses cost a little (the viewer's own API key): videos with English subtitles show them right away
  // as plain English, and the glosses are generated only after a click — like speech recognition. A video
  // done (or started) before just loads.
  function offerGloss(gen, payload, label) {
    pill(`${label} · 生词注释按你的 key 计费（10 分钟约 ¥0.1–0.3）`, {
      sticky: true,
      action: { label: "生成注释", fn: () => startGloss(gen, payload, label) },
    });
  }
  async function startGloss(gen, payload, label) {
    if (gen !== S.gen) return;
    pill("正在开始生成注释…", { sticky: true });
    const r = await bg({ type: "open", payload });
    if (gen !== S.gen) return;
    if (r.error) return pill(serverMsg(r), { sticky: true, action: { label: "重试", fn: () => startGloss(gen, payload, label) } });
    if (r.cached) { S.id = r.id || payload.id; setSaved(r.data); return pill("注释已载入", { hideAfter: 3000 }); }
    follow(gen, r.id || payload.id, "正在生成注释，先处理你正在看的位置…");
  }

  function ytASR(key) {
    const v = findVideo();
    const title = document.title.replace(/^\(\d+\)\s*/, "").replace(/\s*-\s*YouTube$/, "").trim() || key.v;
    return { id: `yt_${key.v}_asr`, title: title.slice(0, 200), duration: (v && isFinite(v.duration) && v.duration) || 0, source: "asr", yt: key.v };
  }

  // the channel of the video on screen (YouTube updates it a moment after switching videos)
  function ytOwner(v) {
    const f = document.querySelector("ytd-watch-flexy");
    if (!f || f.getAttribute("video-id") !== v) return null;
    const a = f.querySelector("ytd-watch-metadata ytd-video-owner-renderer a[href], #owner ytd-channel-name a[href]");
    const href = a && (a.getAttribute("href") || "").replace(/^https?:\/\/(www\.)?youtube\.com/, "").split(/[?#]/)[0];
    if (!href) return null;
    const name = f.querySelector("#owner ytd-channel-name #text, ytd-video-owner-renderer ytd-channel-name a");
    return { key: `yt:${href}`, name: ((name && name.textContent) || "").trim() };
  }

  async function loadBili(gen, key) {
    pill("正在读取 B 站字幕…");
    try {
      const view = await biliJSON(`/x/web-interface/view?bvid=${key.bvid}`);
      if (gen === S.gen) { S.owner = view.owner ? view.owner.mid : null; S.ownerName = (view.owner && view.owner.name) || ""; lastLayout = ""; }
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
        return offerASR(gen, { id: `bili_${key.bvid}_${page.cid}_asr`, title, duration: page.duration || view.duration || 0,
          source: "asr", bvid: key.bvid, p: page.page || key.p }, why);
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
      const label = `已载入${ai ? " B 站 AI 生成的" : " UP 主上传的"}英文字幕`;
      pill(`${label}，正在连接本地程序…`);

      S.id = `bili_${key.bvid}_${page.cid}_${track.lan}`.replace(/[^A-Za-z0-9_.-]/g, "_");
      const meta = { id: S.id, title, duration: page.duration || view.duration || 0, source: ai ? "auto" : "manual" };
      // done before / being generated? (asks only: generating starts with the 生成注释 click)
      const r = await bg({ type: "open", payload: { ...meta, probe: true } });
      if (gen !== S.gen) return;
      if (r.error) return pill(serverMsg(r), { sticky: true });
      if (r.cached) {
        setSaved(r.data);
        return pill("注释已载入（这个视频之前生成过）", { hideAfter: 4000 });
      }
      if (r.running) return follow(gen, S.id, "正在生成注释，先处理你正在看的位置…");
      offerGloss(gen, { ...meta, cues }, label);
    } catch (e) {
      if (gen === S.gen) pill(`读取字幕失败：${e.message || e}`, { sticky: true });
    }
  }

  // ------------------------------------------------- speech recognition ---
  // Videos without English subtitles: the local server downloads the audio and recognises the speech.
  // Started by a click (it costs a little, and most videos without English subtitles are in Chinese),
  // unless it was done before — then the result just loads.
  async function offerASR(gen, payload, why) {
    const probe = await bg({ type: "open", payload: { ...payload, probe: true } });
    if (gen !== S.gen) return;
    if (probe.cached) {
      S.id = payload.id;
      setSaved(probe.data);
      return pill("已载入之前语音识别的英文字幕和注释", { hideAfter: 4000 });
    }
    if (probe.running) return follow(gen, payload.id, "语音识别进行中…");
    if (probe.error && probe.error !== "noasr") return pill(`${why}。${serverMsg(probe)}`, { sticky: true });
    if (probe.error === "noasr") return pill(`${why}。要用语音识别，先在程序设置里填写小米 MiMo 的 API key`, { sticky: true });
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
    if (r.cached) { S.id = payload.id; setSaved(r.data); return pill("已载入", { hideAfter: 3000 }); }
    follow(gen, payload.id, "正在下载音频…");
  }

  function follow(gen, id, msg) {
    S.id = id;
    S.since = 0;
    S.annDone = new Set();
    pill(msg);
    poll(gen);
  }

  function setSaved(data) {
    S.savedVideo = data.status == null || data.status === "complete" ? data.video : null;
    S.savedCues = S.savedVideo ? data.cues : null;
    setCues(data.cues);
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
        } else if (type === "more") {          // speech recognition: more lines cut while the audio streams in
          d.cues.forEach((c, k) => {
            const i = d.from + k, old = S.cues[i];
            S.cues[i] = { s: c.s, e: c.e, text: c.text || (old && old.text) || "", zh: (old && old.zh) || "", notes: (old && old.notes) || [] };
          });
          S.dirty = true;
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
          S.finished = true;
          finished = true;
          S.annDone = null;
          pill(`注释已全部生成（用时 ${d.seconds} 秒），下次打开会直接载入`, { hideAfter: 5000 });
        } else if (type === "fail") {
          finished = true;
          if (d.nosubs && YOUTUBE && S.page) offerASR(gen, ytASR(S.page), "这个视频没有英文字幕");
          else pill(d.msg, d.retry ? { sticky: true, action: { label: "重试", fn: () => { S.key = null; } } } : { sticky: true });
        } else if (type === "status") {
          pill(d.msg);
        }
      }
      computeVisible();
      if (finished) return;
    }
  }

  // ------------------------------------------- which glosses to show (core) ---
  // "forced" (marked 不认识) > "known" (in the library / marked 认识) > level rule ("hard" / "easy")
  function noteState(n) {
    const pref = PREFS[cfg.pref] || PREFS.more;
    return AdaptivePrediction.classify(n, PF.known, PF.unknown, effLevel() * pref.factor);
  }

  // rough base forms of a word as written, to match the library's lemma keys ("decided" -> "decide")
  function lemmaGuesses(w) {
    w = w.toLowerCase().replace(/['’]s$/, "");
    const out = [w];
    const add = (x) => { if (x.length >= 2 && !out.includes(x)) out.push(x); };
    if (w.endsWith("ies")) add(w.slice(0, -3) + "y");
    if (w.endsWith("es")) add(w.slice(0, -2));
    if (w.endsWith("s")) add(w.slice(0, -1));
    if (w.endsWith("ied")) add(w.slice(0, -3) + "y");
    if (w.endsWith("ed")) { add(w.slice(0, -2)); add(w.slice(0, -1)); if (/(.)\1ed$/.test(w)) add(w.slice(0, -3)); }
    if (w.endsWith("ing")) { add(w.slice(0, -3)); add(w.slice(0, -3) + "e"); if (/(.)\1ing$/.test(w)) add(w.slice(0, -4)); }
    return out;
  }

  // Words marked 不认识 that this video has no annotation for: show the dictionary meaning instead.
  function addMarkedWords(c) {
    c.notes = c.notes.filter((n) => !n.local);
    if (!PF.unknown.size) return;
    const extra = [];
    for (const m of c.text.matchAll(WORD_RE)) {
      const s = m.index, e = s + m[0].length;
      if (c.notes.some((n) => n.s < e && s < n.e)) continue;
      const key = lemmaGuesses(m[0]).find((k) => PF.unknown.has(k) && defs[k]);
      if (key) extra.push({ s, e, zh: defs[key], lv: 99999, kind: "word", key, local: true });
    }
    if (extra.length) c.notes = c.notes.concat(extra).sort((a, b) => a.s - b.s);
  }

  function computeVisible() {
    const pref = PREFS[cfg.pref] || PREFS.more;
    const count = new Map();
    S.visible = S.cues.map((c) => {
      addMarkedWords(c);
      return AdaptivePrediction.selectNotes(c.notes, noteState, pref, count);
    });
    S.dirty = true;
  }

  // ------------------------------------------------------------- profile ---
  async function loadProfile() {
    const r = await bg({ type: "profile" });
    PF.lastCheck = Date.now();
    if (r.error || !Array.isArray(r.known)) { PF.ok = false; computeVisible(); return; }
    Object.assign(PF, { ok: true, rev: r.rev, level: r.level, known: new Set(r.known), unknown: new Set(r.unknown),
      marks: new Map([...r.marks.known.map((k) => [k, "known"]), ...r.marks.unknown.map((k) => [k, "unknown"])]) });
    computeVisible();
  }

  async function checkProfile() {          // the profile page or the popup may have changed it
    if (document.visibilityState !== "visible" || Date.now() - PF.lastCheck < 15000) return;
    PF.lastCheck = Date.now();
    const r = await bg({ type: "profile", summary: true });
    if (r.error) { if (PF.ok) { PF.ok = false; computeVisible(); } return; }
    if (r.rev !== PF.rev || !PF.ok) loadProfile();
  }

  async function mark(key, state, word, zhShort) {
    if (state === "unknown" && zhShort && !defs[key]) {
      defs[key] = zhShort;
      chrome.storage.local.set({ asubsDefs: defs });
    }
    // update right away, then let the server recompute the library
    PF.known.delete(key); PF.unknown.delete(key); PF.marks.delete(key);
    if (state === "known") { PF.known.add(key); PF.marks.set(key, "known"); }
    if (state === "unknown") { PF.unknown.add(key); PF.marks.set(key, "unknown"); }
    computeVisible();
    const r = await bg({ type: "mark", payload: { key, state } });
    if (r.error) return pill(`标记没保存：${serverMsg(r)}`, { hideAfter: 5000 });
    const shown = key.replace(/^(sense|phrase):/, "");
    pill(state === "known" ? `已加入熟词库：${shown}，不再注释` : state === "unknown" ? `已标为生词：${shown}，以后总会注释` : `已恢复：${shown}，按画像判断`,
      { hideAfter: 3500 });
    loadProfile();
  }

  // ------------------------------------------------------------ overlay ---
  function findVideo() {
    return document.querySelector(SITE.video);
  }

  function ensureOverlay(video) {
    const host = video.closest(SITE.host) || video.parentElement;
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
    ui.cover = el("div", "asubs-cover");     // frosted board over subtitles printed in the picture
    ui.cover.hidden = true;
    const coverX = el("button", "asubs-cover-x", "×");
    Object.assign(coverX, { type: "button", title: `${SITE.here}不用遮挡板` });
    coverX.setAttribute("aria-label", "关闭遮挡板");
    ui.cover.append(el("span", "asubs-cover-hint", "遮挡板 · 拖动移动 · 拖边缘调大小 · 双击恢复默认 · 按 P 关闭"), coverX,
      ...["t", "b", "l", "r"].map((k) => { const e = el("span", `asubs-cover-edge asubs-cover-${k}`); e.dataset.edge = k; return e; }));
    ui.card = el("div", "asubs-card");
    ui.card.hidden = true;
    ui.card.setAttribute("role", "dialog");
    ui.toast = el("div", "asubs-toast");     // short feedback for shortcuts ("字幕：纯英文"); the pill keeps the status
    ui.toast.hidden = true;
    ui.toast.setAttribute("role", "status");
    ui.root.append(ui.cover, ui.box, ui.pill, ui.card, ui.toast);
    enableDrag();
    enableCover();
    lastLayout = "";
    // clicks on words / the card must not reach the player underneath (it would pause / play / go fullscreen)
    for (const target of [ui.line, ui.card]) {
      for (const ev of ["pointerdown", "mousedown", "mouseup", "dblclick", "click"]) {
        target.addEventListener(ev, (e) => {
          if (target === ui.line && !e.target.closest(".asubs-t")) return;
          e.stopPropagation();
          if (ev === "click" && target === ui.line) { e.preventDefault(); openCard(e.target.closest(".asubs-t")); }
        });
      }
    }
    ui.card.addEventListener("click", onCardClick);
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

  // ------------------------------------------------------------ cover board ---
  // Many Bilibili videos have subtitles printed into the picture (Chinese, English or both). A frosted
  // board covers the area where they usually are (default: 78-95 % of the picture height, middle 80 %).
  // The viewer drags it to move it, drags its edges to resize it; it is remembered per UP 主 (and the
  // last one used is the default for new UP 主s). Double-click: back to the default. ×: no board for
  // this UP 主 (the popup can bring it back). The board sits under Bilibili's control bar (z-index 75),
  // so the progress bar stays usable. On YouTube (few videos have printed subtitles) it is off until the
  // viewer turns it on for a channel in the popup; the same dragging applies there.
  const COVER_DEFAULT = { x0: 0.1, y0: 0.78, x1: 0.9, y1: 0.95 };
  let COVER = {};           // UP 主 -> {x0, y0, x1, y1} (shares of the picture) | {off: true}; "_last": last used
  let POS = {};             // UP 主 -> our line dragged by the viewer (bottom gap as a share of the player height)
  let lastLayout = "";
  const posKey = () => (S.owner != null ? String(S.owner) : "_");
  const posFor = () => (POS[posKey()] != null ? POS[posKey()] : null);
  function coverHere() {        // does this UP 主 / channel get the board (when the board is switched on)?
    const c = COVER[posKey()];
    return c ? !c.off : SITE.coverByDefault;
  }
  function coverRect() {
    if (cfg.cover === "off" || !cfg.enabled || !coverHere()) return null;
    const c = COVER[posKey()];
    return (c && !c.off && c) || COVER._last || COVER_DEFAULT;
  }
  function setCoverHere(on) {
    if (on) saveCover({ ...(COVER._last || COVER_DEFAULT) });
    else if (SITE.coverByDefault) saveCover({ off: true });
    else { delete COVER[posKey()]; chrome.storage.local.set({ asubsCover: COVER }); lastLayout = ""; }      // back to the default: off
  }
  function saveCover(rect) {
    COVER[posKey()] = rect;
    if (!rect.off) COVER._last = rect;
    chrome.storage.local.set({ asubsCover: COVER });
    lastLayout = "";
  }

  // where the picture is drawn inside our overlay (the video element letterboxes it: object-fit contain)
  function pictureRect(video, root) {
    const r = video.getBoundingClientRect();
    const vw = video.videoWidth || 16, vh = video.videoHeight || 9;
    const s = Math.min(r.width / vw, r.height / vh), w = vw * s, h = vh * s;
    return { x: r.left - root.left + (r.width - w) / 2, y: r.top - root.top + (r.height - h) / 2, w, h };
  }

  // Bilibili's control bar (progress bar + buttons) covers the bottom of the player when the mouse is
  // there: our line stays above it, or it could not be clicked or dragged.
  function controlsHeight() {
    const c = ui.host && ui.host.querySelector(SITE.controls);
    const h = c ? c.getBoundingClientRect().height : 0;
    return h > 20 ? h : 56;
  }
  const minBottom = () => controlsHeight() + 10;

  // ---------------------------------------------------- subtitle position ---
  function layout(video) {
    const root = ui.root.getBoundingClientRect();
    if (!root.height) return;
    const pic = pictureRect(video, root);
    const r = coverRect();
    const cover = r && { left: pic.x + r.x0 * pic.w, top: pic.y + r.y0 * pic.h, width: (r.x1 - r.x0) * pic.w, height: (r.y1 - r.y0) * pic.h };
    const low = minBottom(), boxH = ui.box.offsetHeight || 40;
    let bottom = null, mode = "default";
    const manual = posFor();
    if (!cfg.enabled) mode = "off";
    else if (manual != null) { bottom = manual * root.height; mode = "manual"; }
    else if (cfg.pos === "top") mode = "top";
    else if (cover) { bottom = root.height - (cover.top + cover.height / 2) - boxH / 2; mode = "cover"; }   // on the board
    else bottom = Math.max(low, root.height * 0.12);
    if (bottom != null) bottom = Math.max(low, Math.min(root.height * 0.85, bottom));
    const key = [mode, Math.round(bottom || 0), Math.round(boxH), cover ? [cover.left, cover.top, cover.width, cover.height].map(Math.round).join(":") : ""].join(",");
    if (key === lastLayout) return;
    lastLayout = key;
    ui.root.classList.toggle("asubs-manual", mode === "manual");
    ui.box.style.bottom = bottom != null ? `${bottom.toFixed(0)}px` : "";
    ui.cover.hidden = !cover;
    if (cover) Object.assign(ui.cover.style, { left: `${cover.left}px`, top: `${cover.top}px`, width: `${cover.width}px`, height: `${cover.height}px` });
  }

  // board: drag the middle to move, an edge to resize (shares of the picture, kept inside it)
  function enableCover() {
    let op = null;
    const pic = () => pictureRect(findVideo(), ui.root.getBoundingClientRect());
    ui.cover.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".asubs-cover-x")) return;
      e.stopPropagation();
      e.preventDefault();
      const r = coverRect();
      if (!r) return;
      const edgeEl = e.target.closest("[data-edge]");
      op = { x: e.clientX, y: e.clientY, r: { ...r }, edge: edgeEl ? edgeEl.dataset.edge : "", p: pic(), moved: false };
      ui.cover.setPointerCapture(e.pointerId);
      ui.cover.classList.add("asubs-cover-active");
    });
    ui.cover.addEventListener("pointermove", (e) => {
      if (!op) return;
      const dx = (e.clientX - op.x) / op.p.w, dy = (e.clientY - op.y) / op.p.h;
      if (Math.abs(e.clientX - op.x) + Math.abs(e.clientY - op.y) > 3) op.moved = true;
      if (!op.moved) return;
      const r = { ...op.r }, MIN = 0.04;
      if (!op.edge) {                                  // move
        const w = r.x1 - r.x0, h = r.y1 - r.y0;
        r.x0 = Math.max(0, Math.min(1 - w, r.x0 + dx)); r.x1 = r.x0 + w;
        r.y0 = Math.max(0, Math.min(1 - h, r.y0 + dy)); r.y1 = r.y0 + h;
      } else {                                         // resize one edge
        if (op.edge === "t") r.y0 = Math.max(0, Math.min(r.y1 - MIN, r.y0 + dy));
        if (op.edge === "b") r.y1 = Math.min(1, Math.max(r.y0 + MIN, r.y1 + dy));
        if (op.edge === "l") r.x0 = Math.max(0, Math.min(r.x1 - MIN, r.x0 + dx));
        if (op.edge === "r") r.x1 = Math.min(1, Math.max(r.x0 + MIN, r.x1 + dx));
      }
      op.now = r;
      COVER[posKey()] = r;            // live preview (saved on release)
      lastLayout = "";
      layout(findVideo());
    });
    ui.cover.addEventListener("pointerup", (e) => {
      if (!op) return;
      e.stopPropagation();
      ui.cover.classList.remove("asubs-cover-active");
      const o = op;
      op = null;
      if (o.moved && o.now) saveCover(o.now);
    });
    ui.cover.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      if (e.target.closest(".asubs-cover-x")) return;
      COVER._last = { ...COVER_DEFAULT };
      saveCover({ ...COVER_DEFAULT });
      pill("遮挡板已恢复默认位置", { hideAfter: 3000 });
    });
    ui.cover.querySelector(".asubs-cover-x").addEventListener("click", (e) => {
      e.stopPropagation();
      setCoverHere(false);
      pill(`${SITE.here}的视频不再显示遮挡板（按 P 可以再打开）`, { hideAfter: 5000 });
    });
    for (const ev of ["mousedown", "mouseup", "click", "dblclick"]) ui.cover.addEventListener(ev, (e) => e.stopPropagation());
  }

  // our line: drag its background up or down; remembered per UP 主 / channel (never into the control bar)
  function enableDrag() {
    let drag = null;
    ui.box.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".asubs-t") || e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      drag = { y: e.clientY, bottom: parseFloat(getComputedStyle(ui.box).bottom) || 0, moved: false };
      ui.box.setPointerCapture(e.pointerId);
    });
    ui.box.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dy = e.clientY - drag.y;
      if (Math.abs(dy) > 3) drag.moved = true;
      if (!drag.moved) return;
      const h = ui.root.getBoundingClientRect().height;
      const b = Math.max(minBottom(), Math.min(h * 0.85, drag.bottom - dy));
      ui.root.classList.add("asubs-manual");
      ui.box.style.bottom = `${b}px`;
      drag.last = b;
    });
    ui.box.addEventListener("pointerup", (e) => {
      if (!drag) return;
      e.stopPropagation();
      const d = drag;
      drag = null;
      if (!d.moved || d.last == null) return;
      POS[posKey()] = +(d.last / ui.root.getBoundingClientRect().height).toFixed(4);
      chrome.storage.local.set({ asubsPos: POS });
      lastLayout = "";
      pill(`已记住${SITE.here}的字幕位置（双击字幕空白处恢复自动）`, { hideAfter: 4000 });
    });
    ui.box.addEventListener("dblclick", (e) => {
      if (e.target.closest(".asubs-t") || posFor() == null) return;
      e.stopPropagation();
      delete POS[posKey()];
      chrome.storage.local.set({ asubsPos: POS });
      lastLayout = "";
      pill("已恢复自动位置", { hideAfter: 3000 });
    });
    // the subtitle box is ours: its clicks never reach the player underneath
    for (const ev of ["mousedown", "mouseup", "click", "dblclick"]) ui.box.addEventListener(ev, (e) => e.stopPropagation());
  }

  // ---------------------------------------------------------- word card ---
  // Click a word: pause, show its meaning and why it is / isn't glossed, mark 认识 / 不认识.
  const CARD = { open: false, paused: false, key: "", word: "", zh: "", short: "", note: null, ci: -1, defReq: 0 };
  const KIND = { phrase: "短语", sense: "特殊意思" };

  function openCard(tok) {
    if (!tok || S.cur < 0) return;
    const c = S.cues[S.cur];
    const note = tok.dataset.n != null ? c.notes[+tok.dataset.n] : null;
    const word = note ? c.text.slice(note.s, note.e) : tok.dataset.w;
    const key = note ? markKey(note) : (lemmaGuesses(word).find((k) => PF.known.has(k) || PF.unknown.has(k)) || word.toLowerCase());
    if (CARD.open && CARD.key === key) return closeCard();
    const v = findVideo();
    if (!CARD.open && v && !v.paused) { v.pause(); CARD.paused = true; }
    const looking = !note || note.local;
    Object.assign(CARD, { open: true, key, word, note, ci: S.cur, zh: note && !note.local ? note.zh : "", short: note ? note.zh : "",
      lists: null, dict: null, dictState: looking ? "loading" : "", ctx: null, ctxState: looking ? "loading" : "" });
    renderCard();
    placeCard(tok);
    if (!looking) return;
    // A word without an annotation: dictionary meaning (instant, general) + its meaning in this line
    // (asks the annotation model, 1-2 s) — the dictionary alone can be wrong here ("ligature" = 绳 in a video on fonts)
    const req = ++CARD.defReq;
    const live = () => req === CARD.defReq && CARD.open;
    (async () => {
      let d = await bg({ type: "define", word });
      if (!d.zh) {                               // try base forms ("decided" -> "decide")
        for (const g of lemmaGuesses(word).slice(1)) {
          const r2 = await bg({ type: "define", word: g });
          if (r2.zh) { d = r2; if (!note) CARD.key = g; break; }
        }
      }
      if (!live()) return;
      CARD.dict = d.zh ? d : null;
      CARD.dictState = d.zh ? "done" : "none";
      CARD.lists = d.lists || null;
      renderCard();
    })();
    bg({ type: "explain", payload: { word, sentence: S.cues[S.cur] ? S.cues[S.cur].text : "" } }).then((r) => {
      if (!live()) return;
      if (r.zh) { CARD.ctx = r; CARD.ctxState = "done"; }
      else { CARD.ctxState = r.error ? "error" : "none"; CARD.ctxError = r.error === "noserver" || r.error === "oldserver" ? "" : (r.error || ""); }
      renderCard();
    });
  }

  function cardReason() {
    const n = CARD.note, k = CARD.key, mk = PF.marks.get(k);
    if (!PF.ok) return "打开 Adaptive Subs 程序后可以标记";
    if (mk === "unknown") return "你标记过「不认识」，所以总会注释";
    if (mk === "known") return "你标记过「认识」，所以不注释";
    if (PF.unknown.has(k)) return "词汇测试里选了「不认识」，所以总会注释";
    if (PF.known.has(k)) return n && n.kind !== "word" ? "你标记过认识这个用法" : "在你的熟词库里（考试词表 / 常见词 / 测试），所以不注释";
    if (!n) return "这一集没有给它注释";
    return noteState(n) === "hard" ? `难度约 ${n.lv.toLocaleString("en-US")} 词，高于你的水平（≈ ${effLevel().toLocaleString("en-US")}），所以注释`
      : `难度约 ${n.lv.toLocaleString("en-US")} 词，按你的水平（≈ ${effLevel().toLocaleString("en-US")}）不注释`;
  }

  function renderCard() {
    const n = CARD.note, mk = PF.marks.get(CARD.key);
    const known = n && n.kind !== "word" ? "我懂这个意思" : "我认识";
    const dim = (t) => h("div", "asubs-card-dim", t);
    const zhLine = (pos, zh) => h("div", "asubs-card-zh", pos ? h("span", "asubs-card-pos", pos) : null, zh);
    let meaning;
    if (n && !n.local) {                       // annotated: the gloss already is the meaning in this line
      meaning = [zhLine("", n.zh), dim("（这句话里的意思）")];
    } else {
      const c = CARD.ctx, d = CARD.dict;
      const ctxLine = CARD.ctxState === "done" ? [zhLine(c.pos, c.zh), dim(`（这句话里的意思${c.note ? `；${c.note}` : ""}）`)]
        : CARD.ctxState === "loading" ? [dim("正在查这句话里的意思…")]
        : CARD.ctxState === "error" && CARD.ctxError ? [dim(`这句话里的意思没查到：${CARD.ctxError}`)] : [];
      const dictLine = CARD.dictState === "done" ? [h("div", "asubs-card-dict", h("span", "asubs-card-dim", "词典"), ` ${d.zh}`)]
        : CARD.dictState === "loading" ? [] : [dim("词典里没有这个词")];
      const nothing = CARD.dictState === "none" && (CARD.ctxState === "none" || CARD.ctxState === "error");
      meaning = nothing ? [h("div", "asubs-card-zh asubs-card-none", "没查到释义，可以点右下角「查词典」"), ...ctxLine] : [...ctxLine, ...dictLine];
    }
    const btn = (act, text, pressed) => {
      const b = h("button", null, text);
      b.type = "button";
      b.dataset.act = act;
      if (pressed != null) { b.setAttribute("aria-pressed", String(pressed)); b.disabled = !PF.ok; }
      return b;
    };
    const x = btn("close", "×");
    x.className = "asubs-card-x";
    x.setAttribute("aria-label", "关闭");
    const dict = h("a", null, "查词典 ↗");
    Object.assign(dict, { href: `https://dict.youdao.com/result?word=${encodeURIComponent(CARD.word)}&lang=en`, target: "_blank", rel: "noopener" });
    ui.card.replaceChildren(
      h("div", "asubs-card-head", h("span", "asubs-card-word", CARD.word), n && KIND[n.kind] ? h("span", "asubs-card-kind", KIND[n.kind]) : null, x),
      ...meaning,
      ...(CARD.lists && CARD.lists.length ? [dim(`考试词表：${CARD.lists.join("、")}`)] : []),
      h("div", "asubs-card-why", cardReason()),
      h("div", "asubs-card-btns", btn("known", known, mk === "known"), btn("unknown", "我不认识", mk === "unknown"),
        mk ? btn("clear", "恢复默认") : null, dict));
    ui.card.hidden = false;
  }

  function placeCard(tok) {
    const root = ui.root.getBoundingClientRect(), t = tok.getBoundingClientRect(), cw = ui.card.offsetWidth, ch = ui.card.offsetHeight;
    const left = Math.max(8, Math.min(root.width - cw - 8, t.left - root.left + t.width / 2 - cw / 2));
    const above = t.top - root.top - ch - 10;
    const top = cfg.pos === "top" || above < 8 ? t.bottom - root.top + 10 : above;
    ui.card.style.left = `${left}px`;
    ui.card.style.top = `${Math.max(8, Math.min(root.height - ch - 8, top))}px`;
  }

  function closeCard() {
    if (!CARD.open) return;
    CARD.open = false; CARD.defReq++;
    ui.card.hidden = true;
    const v = findVideo();
    if (CARD.paused && v) v.play().catch(() => {});
    CARD.paused = false;
  }

  function onCardClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (act === "close") return closeCard();
    const st = act === "clear" ? "clear" : PF.marks.get(CARD.key) === act ? "clear" : act;
    // gloss shown later for a word marked 不认识: its meaning in the line where it was met, else the dictionary's
    const short = (CARD.ctx && CARD.ctx.zh) || (CARD.dict && CARD.dict.short) || CARD.short;
    mark(CARD.key, st, CARD.word, short);
    closeCard();
  }

  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && CARD.open) closeCard(); }, true);

  // ------------------------------------------------------------ shortcuts ---
  // Only keys neither site uses (Bilibili: Q W E R G D F M [ ] Enter, arrows, Space…; YouTube: K J L C F T I M
  // , . < > 0-9 + -…), only while our subtitles are showing for this video, never while typing in a box.
  // Physical keys (e.code), so a Chinese input method or Caps Lock doesn't matter.
  function typingIn(e) {
    const t = (e.composedPath && e.composedPath()[0]) || e.target;   // Bilibili's comment box is inside shadow DOM
    return !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""));
  }
  function shortcutsOn() {
    return cfg.enabled && S.cues.length > 0 && !!ui.root && ui.root.isConnected && !ui.root.classList.contains("asubs-ad");
  }
  function toast(text, ms = 1400) {
    if (!ui.toast) return;
    clearTimeout(ui.toastTimer);
    ui.toast.textContent = text;
    ui.toast.hidden = false;
    ui.toastTimer = setTimeout(() => { ui.toast.hidden = true; }, ms);
  }
  function cycleMode() {
    cfg.mode = MODES[(MODES.indexOf(cfg.mode) + 1) % MODES.length];
    try { chrome.storage.local.set({ settings: cfg }); } catch { /* extension reloaded: still switch on this page */ }
    computeVisible();
    toast(`字幕：${MODE_NAMES[cfg.mode]}`);
  }
  function startPeek() {
    const v = findVideo();
    const i = S.cur >= 0 ? S.cur : v ? cueAt(v.currentTime || 0) : -1;   // in a silence: the line just spoken
    if (i < 0) return toast("还没到第一句字幕");
    PEEK.wasSticky = PEEK.sticky && PEEK.line === i;    // tapping again on the same line hides it
    Object.assign(PEEK, { hold: true, line: i, downAt: performance.now() });
    S.dirty = true;
  }
  function endPeek(tap) {
    if (!PEEK.hold) return;
    PEEK.hold = false;
    PEEK.sticky = tap && !PEEK.wasSticky;
    S.dirty = true;
  }
  // P: cover board on / off for this UP 主 / channel (remembered); works without our subtitles too
  function toggleCover() {
    const on = !(cfg.cover !== "off" && coverHere());
    if (on && cfg.cover === "off") {           // switched off for every video in the popup: switch it back on
      cfg.cover = "on";
      try { chrome.storage.local.set({ settings: cfg }); } catch { /* extension reloaded */ }
    }
    try { setCoverHere(on); } catch { /* extension reloaded: still shows / hides on this page */ }
    lastLayout = "";
    toast(on ? `遮挡板：开（${SITE.here}的视频会记住）` : "遮挡板：关");
  }
  window.addEventListener("keydown", (e) => {
    if (labelPlayback.active) return;
    if (e.code !== "KeyZ" && e.code !== "KeyB" && e.code !== "KeyP") return;
    if (e.ctrlKey || e.altKey || e.metaKey || e.shiftKey || e.isComposing || typingIn(e)) return;
    if (e.code === "KeyP") {
      if (!cfg.enabled || !ui.root || !ui.root.isConnected || ui.root.classList.contains("asubs-ad") || !S.key) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (!e.repeat) toggleCover();
      return;
    }
    if (!shortcutsOn()) {
      // on a video page without our subtitles: say why nothing happens (the key still goes to the site)
      if (!e.repeat && cfg.enabled && ui.root && ui.root.isConnected && !S.cues.length) toast("这个视频还没有英文字幕，快捷键暂时用不了", 2500);
      return;
    }
    e.preventDefault();
    e.stopImmediatePropagation();       // ours: the site's own handlers don't see it
    if (e.repeat) return;               // holding the key sends repeats
    if (e.code === "KeyB") cycleMode();
    else startPeek();
  }, true);
  window.addEventListener("keyup", (e) => {
    if (labelPlayback.active) return;
    if (e.code !== "KeyZ" || !PEEK.hold) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    endPeek(performance.now() - PEEK.downAt < 300);
  }, true);
  // switching tabs / windows while holding Z: the keyup never comes
  window.addEventListener("blur", () => endPeek(false));
  document.addEventListener("visibilitychange", () => { if (document.hidden) endPeek(false); });
  document.addEventListener("pointerdown", (e) => {
    if (CARD.open && ui.card && !ui.card.contains(e.target) && !(e.target.closest && e.target.closest(".asubs-t"))) closeCard();
  }, true);

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
    if (labelPlayback.active) {
      ui.line.className = "asubs-line";
      ui.line.textContent = c.text;
      ui.zh.hidden = true;
      return;
    }
    const style = cfg.mode === "adaptive" ? cfg.gloss : "none";
    ui.line.className = "asubs-line" + (style === "paren" ? " asubs-paren" : "") + (style === "over" && vis.size ? " asubs-has-over" : "");
    ui.line.replaceChildren();
    // every word is a clickable token: annotated ones carry their note index, others just the word
    const plainText = (s) => {
      let last = 0;
      for (const m of s.matchAll(WORD_RE)) {
        ui.line.append(s.slice(last, m.index));
        const t = el("span", "asubs-t", m[0]);
        t.dataset.w = m[0];
        ui.line.append(t);
        last = m.index + m[0].length;
      }
      ui.line.append(s.slice(last));
    };
    let pos = 0;
    c.notes.forEach((n, k) => {
      if (n.s < pos || n.e > c.text.length) return;
      plainText(c.text.slice(pos, n.s));
      const word = c.text.slice(n.s, n.e);
      if (style !== "none" && vis.has(k)) {
        const g = el("span", (style === "over" ? "asubs-g" : "asubs-p") + " asubs-t" + (noteState(n) === "forced" ? " asubs-forced" : ""));
        g.append(el("span", "asubs-w", word), el("span", style === "over" ? "asubs-gz" : "asubs-pz", style === "over" ? n.zh : `(${n.zh})`));
        g.dataset.n = k;
        ui.line.append(g);
      } else {
        const t = el("span", "asubs-t", word);
        t.dataset.n = k;
        ui.line.append(t);
      }
      pos = n.e;
    });
    plainText(c.text.slice(pos));
    const peek = PEEK.hold || (PEEK.sticky && PEEK.line === i);
    const wait = peek && !c.zh;             // translations arrive after the glosses
    const zh = cfg.mode === "bilingual" || peek ? (c.zh || (wait ? "这句的中文翻译还没生成好" : "")) : "";
    ui.zh.textContent = zh;
    ui.zh.hidden = !zh;
    ui.zh.classList.toggle("asubs-zh-wait", wait);
  }

  function cueAt(t) {      // last line starting at or before t (binary search)
    let lo = 0, hi = S.cues.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (S.cues[mid].s <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  function labelAvailable() {
    return cfg.enabled && S.savedVideo?.id === S.id && Number.isFinite(S.savedVideo?.duration) &&
      S.savedVideo.duration > 0 && S.cues.length > 0 && pageKey()?.str === S.key;
  }

  const labelPlayback = installLabelPlayback({
    getClip: () => labelAvailable() ? {
      generation: S.gen, mediaKey: S.key, title: S.savedVideo.title || S.id,
      candidates: S.savedCues.map(c => (c.notes || []).map(({s, e, kind, key, lv}) => ({s, e, kind, key, lv}))),
      sheet: { version: 1, video: S.id, duration: S.savedVideo.duration,
        cues: S.cues.map(({ s, e, text }) => ({ s, e, text, unknown: null })) },
    } : null,
    isCurrent: clip => labelAvailable() && S.gen === clip.generation && S.id === clip.sheet.video,
    getVideo: findVideo,
    isAd: () => YOUTUBE && !!findVideo()?.closest(".ad-showing"),
    setBlind: on => {
      document.documentElement.classList.toggle("asubs-labeling", on);
      if (on) {
        CARD.paused = false; closeCard();
        PEEK.hold = false; PEEK.sticky = false;
        if (ui.line) ui.line.replaceChildren();
      }
      S.dirty = true;
    },
  });

  let orphanNoted = false;
  function tick() {
    // the extension was reloaded or updated: this copy keeps showing subtitles but is the old code and can't
    // reach the program any more — the page has to be refreshed to get the new one
    if (!orphanNoted && !(chrome.runtime && chrome.runtime.id)) {
      orphanNoted = true;
      pill(SERVER_MSG.ext, { sticky: true });
    }
    labelPlayback.tick();
    const k = pageKey();
    if ((k && k.str) !== S.key) {           // both sites change video / part without reloading the page
      if (k) load(k); else { S.key = null; S.gen++; S.cues = []; }
    }
    if (YOUTUBE && k && S.owner == null) {
      const o = ytOwner(k.v);
      if (o) { S.owner = o.key; S.ownerName = o.name; lastLayout = ""; }
    }
    document.documentElement.classList.toggle("asubs-hide-native", cfg.enabled && cfg.hideNative && S.cues.length > 0);
    const video = findVideo();
    if (!video) return;
    ensureOverlay(video);
    // YouTube plays ads in the same <video>: its time is the ad's, so nothing of ours shows meanwhile
    const ad = YOUTUBE && ui.host.classList.contains("ad-showing");
    ui.root.classList.toggle("asubs-ad", ad);
    if (ad) return;
    layout(video);
    const t = video.currentTime || 0;
    const i = S.cues.length ? cueAt(t) : -1;
    let live = i >= 0 && t <= S.cues[i].e + GAP_GRACE ? i : -1;   // silences: nothing on screen
    // peeking at the Chinese (Z): the line just spoken stays on screen through the silence after it
    if (PEEK.sticky && i !== PEEK.line) { PEEK.sticky = false; S.dirty = true; }
    if (live < 0 && i >= 0 && (PEEK.hold || PEEK.sticky)) live = i;
    if (live >= 0 && !keysHint) {
      keysHint = true;
      try { chrome.storage.local.set({ asubsKeysHint: 1 }); } catch { /* extension reloaded */ }
      toast("快捷键：按住 Z 看这一句的中文 · 按 B 切换字幕模式", 6000);
    }
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
  chrome.storage.local.get(["settings", "asubsDefs", "asubsPos", "asubsCover", "asubsKeysHint"], (r) => {
    keysHint = !!(r && r.asubsKeysHint);
    POS = (r && r.asubsPos) || {};
    COVER = (r && r.asubsCover) || {};
    cfg = { ...DEFAULTS, ...((r && r.settings) || {}) };
    if (!LEVELS.includes(cfg.level)) cfg.level = DEFAULTS.level;
    defs = (r && r.asubsDefs) || {};
    computeVisible();
    loadProfile();
    setInterval(tick, 100);
    setInterval(checkProfile, 5000);
    document.addEventListener("visibilitychange", () => { PF.lastCheck = 0; checkProfile(); });
  });
  // the popup asks whose video this is, and turns the cover board on / off for that UP 主 / channel
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (sender.id !== chrome.runtime.id || !msg) return;
    if (msg.type === "where") {
      const send = () => reply({ videoPage: !!pageKey(), canLabel: !!labelAvailable(), here: SITE.here,
        owner: S.key && S.owner != null ? String(S.owner) : "", name: S.ownerName || "", cover: coverHere() });
      // Use the finalized on-disk cue list, which can differ from the streaming ASR lines.
      if (S.finished && !S.savedVideo && cfg.enabled && S.key === pageKey()?.str) {
        const gen = S.gen, id = S.id;
        const payload = { id, probe: true };
        if (YOUTUBE && id === S.page?.v) payload.source = "youtube";
        bg({ type: "open", payload }).then(r => {
          if (gen === S.gen && r.cached && r.data?.video?.id === id) setSaved(r.data);
        }).finally(send);
        return true;
      }
      send();
    } else if (msg.type === "cover" && S.key && S.owner != null) {
      setCoverHere(!!msg.on);
      reply({ ok: true, cover: coverHere() });
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.profileRev) { loadProfile(); return; }      // the popup changed the level
    if (changes.asubsCover) { COVER = changes.asubsCover.newValue || {}; lastLayout = ""; }
    if (!changes.settings) return;
    const wasOn = cfg.enabled;
    cfg = { ...DEFAULTS, ...(changes.settings.newValue || {}) };
    lastLayout = "";
    computeVisible();
    showPill();
    if (cfg.enabled && !wasOn) { S.key = null; }      // turned back on: load this video's subtitles
  });
})();
