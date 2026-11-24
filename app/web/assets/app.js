/* Adaptive Subs player.
   Data: data/index.json -> [{id, title, file}], data/<file> -> {video, cues:[{s,e,text,zh,notes:[{s,e,zh,lv,kind,key}]}]}
   Each note has a difficulty level; what is shown is decided here, from the viewer's level and
   personal marks (known / always show), with a repeat cap and a per-line cap. */
(() => {
  "use strict";

  const LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000];
  const GAP_GRACE = 0.6;  // seconds a line stays visible after it ends, then the screen goes blank
  // How eagerly to gloss. Default "more": better a few known words glossed than unknown ones missed.
  //   factor: a note shows when its level > your vocabulary × factor
  //   perLine / repeat: max glosses per line / times the same word is glossed per video
  const PREFS = {
    more: { factor: 0.7, perLine: 4, repeat: 3 },
    normal: { factor: 1.0, perLine: 3, repeat: 2 },
    less: { factor: 1.3, perLine: 2, repeat: 1 },
  };
  const STORE_KEY = "adaptive-subs:v2";
  const KIND_LABEL = { word: "单词", phrase: "短语", sense: "熟词僻义" };

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const params = new URLSearchParams(location.search);

  // ------------------------------------------------------------- state ---
  const saved = loadStore();
  const S = {
    video: null,
    cues: [],
    level: LEVELS.includes(saved.level) ? saved.level : 5000,
    mode: saved.mode || "adaptive",          // adaptive | plain | bilingual
    gloss: saved.gloss || "over",            // over | paren
    pref: PREFS[saved.pref] ? saved.pref : "more",   // more | normal | less (how many glosses)
    subPos: saved.subPos || "inside",        // inside (over the video) | below
    autoPause: !!saved.autoPause,
    known: new Set(saved.known || []),       // never gloss
    unknown: new Set(saved.unknown || []),   // always gloss
    book: Array.isArray(saved.book) ? saved.book : [],
    introSeen: !!saved.introSeen,
    test: saved.test || null,
    tab: saved.tab || "transcript",
    visible: [],                             // per cue: Set of note indices shown
    cur: -1,                                 // current cue index
    gap: false,                              // true during a silence after the current line
    annDone: null,                           // while generating live: Set of lines whose annotations arrived
    progress: null,                          // live generation progress {done, total, finished}
    statusMsg: "",                           // message shown before subtitles arrive
  };
  const isDone = (ci) => !S.annDone || S.annDone.has(ci);
  const isHard = (n) => n.lv > S.level * PREFS[S.pref].factor;   // above the viewer's level?
  // marks / known-word library are keyed by word; phrases and special senses get their own keys,
  // so knowing a word never hides its uncommon meaning
  const markKey = (n) => (n.kind === "word" ? n.key : `${n.kind}:${n.key}`);
  // Opened from the local program: the profile (level + known-word library + marks) lives there,
  // shared with the Bilibili extension and the profile page. On GitHub Pages: this browser only.
  let shared = null;
  let bookKeys = new Set(S.book.map((b) => b.key));

  function loadStore() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
  }
  function save() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        level: S.level, pref: S.pref, mode: S.mode, gloss: S.gloss, subPos: S.subPos, autoPause: S.autoPause,
        known: [...S.known], unknown: [...S.unknown], book: S.book,
        introSeen: S.introSeen, test: S.test, tab: S.tab,
      }));
    } catch { /* storage unavailable: settings just won't persist */ }
  }

  // ----------------------------------------------------------- helpers ---
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (t) => { t = Math.max(0, Math.floor(t)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };
  const fmtNum = (n) => n.toLocaleString("en-US");
  const nearestLevel = (x) => LEVELS.reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
  const shuffle = (a) => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
  const sample = (a, n) => shuffle(a).slice(0, n);

  // ------------------------------------------------ visibility (core rule) ---
  function computeVisible() {
    const count = new Map();
    S.visible = S.cues.map((c) => {
      const cand = [];
      c.notes.forEach((n, i) => {
        const mk = markKey(n);
        const forced = S.unknown.has(mk);
        if (!forced && S.known.has(mk)) return;
        if (forced || isHard(n)) cand.push({ i, forced, lv: n.lv, key: mk });
      });
      cand.sort((a, b) => (b.forced - a.forced) || (b.lv - a.lv));
      const keep = new Set();
      for (const x of cand) {
        if (keep.size >= PREFS[S.pref].perLine) break;
        const seen = count.get(x.key) || 0;
        if (!x.forced && seen >= PREFS[S.pref].repeat) continue;
        count.set(x.key, seen + 1);
        keep.add(x.i);
      }
      return keep;
    });
  }

  // ---------------------------------------------------------- rendering ---
  const WORD_RE = /[A-Za-z][A-Za-z'’-]*/g;

  function wrapWords(text, ci) {
    let out = "", last = 0;
    for (const m of text.matchAll(WORD_RE)) {
      out += plain(text.slice(last, m.index));
      const w = m[0].replace(/[-']+$/, "");
      const cls = bookKeys.has(w.toLowerCase()) ? "tok inbook" : "tok";
      out += `<span class="${cls}" data-c="${ci}" data-w="${esc(w)}">${esc(m[0])}</span>`;
      last = m.index + m[0].length;
    }
    return out + plain(text.slice(last));
  }
  // non-word text: show YouTube's speaker-change marker ">>" as a dialogue dash
  const plain = (s) => esc(s).replace(/&gt;&gt;\s*/g, "– ");

  // style: "over" | "paren" | "none"
  function lineHTML(ci, style) {
    const c = S.cues[ci], t = c.text, vis = S.visible[ci];
    let html = "", pos = 0;
    c.notes.forEach((n, i) => {
      html += wrapWords(t.slice(pos, n.s), ci);
      const word = esc(t.slice(n.s, n.e));
      const extra = (S.unknown.has(markKey(n)) ? " forced" : "") + (bookKeys.has(n.key) ? " inbook" : "");
      const attrs = `data-c="${ci}" data-n="${i}"`;
      if (style !== "none" && vis.has(i)) {
        html += style === "over"
          ? `<span class="tok g${extra}" ${attrs}><span class="w">${word}</span><span class="zh">${esc(n.zh)}</span></span>`
          : `<span class="tok p${extra}" ${attrs}><span class="w">${word}</span><span class="pz">(${esc(n.zh)})</span></span>`;
      } else {
        html += `<span class="tok${bookKeys.has(n.key) ? " inbook" : ""}" ${attrs}>${word}</span>`;
      }
      pos = n.e;
    });
    return html + wrapWords(t.slice(pos), ci);
  }

  // ---- subtitle position: inside the video (overlay) or below it; fullscreen is always inside
  const frame = () => $("#playerFrame");
  const isFs = () => document.fullscreenElement === frame() || frame().classList.contains("pseudo-fs");
  // On a narrow (phone) video, subtitles inside would cover too much of the picture,
  // so they go below it there — except in fullscreen.
  const subsInside = () => isFs() || (S.subPos === "inside" && frame().clientWidth >= 560);

  function placeSubs() {
    const st = $("#stageText");
    const target = subsInside() ? $("#overlaySubs") : $("#stage");
    if (st.parentElement !== target) target.prepend(st);
    $("#stage").classList.toggle("bar-only", subsInside());
  }

  function renderStage() {
    const line = $("#line"), zh = $("#lineZh"), meta = $("#lineMeta");
    const ci = S.cur;
    // inside the video, show the box only while a line is being spoken (not during silences),
    // or while a new video's subtitles are still being fetched (progress / error + retry)
    const loading = !S.cues.length && !!S.statusMsg;
    $("#overlaySubs").hidden = !(subsInside() && ((ci >= 0 && S.cues[ci] && !S.gap) || loading));
    if (ci >= 0 && S.cues[ci] && S.gap) {          // silence after a line: show nothing
      line.className = "line";
      line.innerHTML = "";
      zh.hidden = true;
      meta.textContent = `第 ${ci + 1} / ${S.cues.length} 句 · ${fmtTime(S.cues[ci].s)}`;
      return;
    }
    if (ci < 0 || !S.cues[ci]) {
      line.className = "line";
      line.innerHTML = `<span class="placeholder">${esc(S.cues.length ? "▶ 播放视频，字幕会显示在这里" : S.statusMsg || "正在加载字幕…")}</span>`
        + (S.retryId && !S.cues.length ? ` <button class="btn btn-primary retry-btn" type="button">重试</button>` : "");
      const rb = $(".retry-btn", line);
      if (rb) rb.onclick = () => openVideo(S.retryId);
      zh.hidden = true;
      meta.textContent = S.cues.length ? `共 ${S.cues.length} 句` : "";
      return;
    }
    const style = S.mode === "adaptive" ? S.gloss : "none";
    line.className = "line" + (style === "paren" ? " paren" : "");
    line.innerHTML = lineHTML(ci, style);
    zh.hidden = S.mode !== "bilingual" || !S.cues[ci].zh;
    zh.textContent = S.cues[ci].zh || "";
    meta.textContent = `第 ${ci + 1} / ${S.cues.length} 句 · ${fmtTime(S.cues[ci].s)}${isDone(ci) ? "" : " · 这句的注释生成中…"}`;
  }

  function renderDensity() {
    const total = S.visible.reduce((a, s) => a + s.size, 0);
    const per = S.cues.length ? total / S.cues.length : 0;
    const [cls, label] = per < 0.25 ? ["pill-warn", "偏少"] : per <= 1 ? ["pill-good", "合适"] : ["pill-bad", "偏多"];
    let html = S.mode === "adaptive"
      ? `<span>本集显示 <b>${total}</b> 个注释 · 平均每句 ${per.toFixed(2)} 个</span>${S.annDone ? "" : `<span class="pill ${cls}">密度${label}</span>`}`
      : `<span>${S.mode === "plain" ? "纯英文模式：不显示注释" : "中英对照模式：显示整句翻译，用来对比"}</span>`;
    if (S.progress) {
      const p = S.progress, pct = Math.round((100 * p.done) / Math.max(1, p.total));
      html += p.finished
        ? `<span class="gen done"><span class="gen-bar"><span style="width:100%"></span></span>注释已全部生成${p.seconds ? `（用时 ${p.seconds} 秒）` : ""}</span>`
        : `<span class="gen"><span class="gen-bar"><span style="width:${pct}%"></span></span>注释生成中 ${pct}% · 优先处理你正在看的位置</span>`;
    }
    if (S.retryId) html += '<button type="button" class="btn btn-primary" data-generation="retry">继续处理缺失字幕</button>';
    else if (es) html += '<button type="button" class="btn btn-ghost" data-generation="cancel">取消生成</button>';
    $("#density").innerHTML = html;
    $("#levelValue").textContent = `≈ ${fmtNum(S.level)} 词`;
    $("#levelRange").value = LEVELS.indexOf(S.level);
    $("#levelRange").setAttribute("aria-valuetext", `${S.level} 词`);
  }

  function renderControls() {
    $$("#modeSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === S.mode)));
    $$("#glossSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.gloss === S.gloss)));
    $$("#posSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.pos === S.subPos)));
    $$("#prefSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.pref === S.pref)));
    $("#prefSeg").closest(".ctl-group").style.opacity = S.mode === "adaptive" ? "" : "0.5";
    placeSubs();
    $("#glossSeg").closest(".ctl-group").style.opacity = S.mode === "adaptive" ? "" : "0.5";
    $("#autoPause").checked = S.autoPause;
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === S.tab)));
    $$(".tab-panel").forEach((p) => { p.hidden = p.id !== `tab-${S.tab}`; });
    $("#bookCount").textContent = S.book.length;
  }

  // transcript
  function trText(ci) {
    const c = S.cues[ci];
    if (S.mode !== "adaptive") return plain(c.text);
    let html = "", pos = 0;
    c.notes.forEach((n, i) => {
      if (!S.visible[ci].has(i)) return;
      html += plain(c.text.slice(pos, n.s)) + esc(c.text.slice(n.s, n.e)) + `<span class="tz${S.unknown.has(markKey(n)) ? " forced" : ""}">(${esc(n.zh)})</span>`;
      pos = n.e;
    });
    return html + plain(c.text.slice(pos));
  }

  function renderTranscript(scrollToActive = true) {
    const panel = $("#tab-transcript");
    panel.innerHTML = S.cues.map((c, ci) => `
      <div class="tr-row${isDone(ci) ? "" : " pending"}" data-c="${ci}">
        <span class="tr-time">${fmtTime(c.s)}</span>
        <div><div class="tr-text">${trText(ci)}</div>${S.mode === "bilingual" && c.zh ? `<div class="tr-zh">${esc(c.zh)}</div>` : ""}</div>
      </div>`).join("");
    markActiveRow(scrollToActive);
  }

  let lastUserScroll = 0;
  function markActiveRow(force) {
    const panel = $("#tab-transcript");
    const prev = $(".tr-row.active", panel);
    const row = S.cur >= 0 ? panel.children[S.cur] : null;
    if (prev && prev !== row) prev.classList.remove("active");
    if (!row) return;
    row.classList.add("active");
    if (!panel.hidden && (force || Date.now() - lastUserScroll > 4000)) {
      panel.scrollTop = row.offsetTop - panel.clientHeight / 2 + row.offsetHeight / 2;
    }
  }

  // pre-watch list
  function previewList() {
    const agg = new Map();
    S.cues.forEach((c, ci) => c.notes.forEach((n) => {
      const mk = markKey(n);
      if ((S.known.has(mk) && !S.unknown.has(mk)) || !(isHard(n) || S.unknown.has(mk))) return;
      let a = agg.get(mk);
      if (!a) agg.set(mk, (a = { key: mk, zh: n.zh, kind: n.kind, lv: n.lv, count: 0, first: ci, n }));
      a.count++;
    }));
    return { list: [...agg.values()].sort((a, b) => (b.count - a.count) || (b.lv - a.lv)), total: agg.size };
  }

  function renderPreview() {
    const panel = $("#tab-preview");
    const { list, total } = previewList();
    if (!total) {
      panel.innerHTML = `<div class="empty"><div class="empty-title">这集没有需要提示的词</div>按你现在的词汇量，这集基本都能看懂。可以把词汇量调低一点试试。</div>`;
      return;
    }
    panel.innerHTML = `
      <p class="panel-intro">按你现在的词汇量（<b>≈ ${fmtNum(S.level)}</b>），这集最值得先认识的 ${Math.min(15, total)} 个词。已经认识的点「认识」，看的时候就不再提示。</p>
      ${list.slice(0, 15).map((a) => `
        <div class="wrow">
          <div><div class="w-term">${esc(a.key)}</div><div class="w-zh">${esc(a.zh)}</div></div>
          <div class="w-actions">
            <button class="btn btn-ghost btn-sm" data-act="know" data-key="${esc(a.key)}">认识</button>
            <button class="btn btn-ghost btn-sm" data-act="book" data-c="${a.first}" data-key="${esc(a.key)}">${bookKeys.has(a.key) ? "✓ 已收藏" : "+ 生词本"}</button>
          </div>
          <div class="w-meta">
            ${a.kind !== "word" ? `<span class="tag tag-${a.kind}">${KIND_LABEL[a.kind]}</span>` : ""}
            <span>出现 ${a.count} 次</span>
            <button class="time-link" data-act="seek" data-c="${a.first}">▶ ${fmtTime(S.cues[a.first].s)}</button>
          </div>
        </div>`).join("")}
      <div class="panel-foot"><span>本集共 ${total} 个需要提示的词 / 短语</span></div>`;
  }

  // wordbook
  function renderBook() {
    const panel = $("#tab-book");
    if (!S.book.length) {
      panel.innerHTML = `<div class="empty"><div class="empty-title">生词本还是空的</div>点击字幕里的词，选「加入生词本」。单词会连同原句和时间点一起保存，之后可以一键回到原片段重听。</div>`;
      return;
    }
    const items = S.book.map((b, i) => ({ b, i })).reverse();
    panel.innerHTML = items.map(({ b, i }) => {
      const sameVideo = S.video && b.vid === S.video.id;
      return `
      <div class="wrow">
        <div><div class="w-term">${esc(b.term)}</div>${b.zh ? `<div class="w-zh">${esc(b.zh)}</div>` : ""}</div>
        <div class="w-actions"><button class="x-btn" data-act="unbook" data-i="${i}" aria-label="从生词本删除" title="删除">×</button></div>
        <div class="w-sent">${markTerm(b.sentence, b.term)}</div>
        <div class="w-meta">
          ${b.kind && b.kind !== "word" ? `<span class="tag tag-${b.kind}">${KIND_LABEL[b.kind]}</span>` : ""}
          ${sameVideo ? `<button class="time-link" data-act="seekt" data-t="${b.t}">▶ 回到 ${fmtTime(b.t)} 重听</button>` : ""}
        </div>
      </div>`;
    }).join("") + `<div class="panel-foot"><span>保存在这台设备的浏览器里</span><button class="btn btn-ghost btn-sm" data-act="export">导出 CSV（可导入 Anki）</button></div>`;
  }

  function markTerm(sentence, term) {
    const i = sentence.toLowerCase().indexOf(term.toLowerCase());
    if (i < 0) return esc(sentence);
    return esc(sentence.slice(0, i)) + `<mark>${esc(sentence.slice(i, i + term.length))}</mark>` + esc(sentence.slice(i + term.length));
  }

  function renderAll(scrollTranscript = true) {
    computeVisible();
    renderControls();
    renderDensity();
    renderStage();
    renderTranscript(scrollTranscript);
    renderPreview();
    renderBook();
  }

  // ------------------------------------------------------ personal marks ---
  const shownKey = (k) => k.replace(/^(sense|phrase):/, "");
  function markKnown(key, silent) {
    S.known.add(key); S.unknown.delete(key); save(); renderAll(); pushMark(key, "known");
    if (!silent) toast(`已标为认识：${shownKey(key)}，之后不再提示`, () => { S.known.delete(key); save(); renderAll(); pushMark(key, "clear"); });
  }
  function markUnknown(key) {
    S.unknown.add(key); S.known.delete(key); save(); renderAll(); pushMark(key, "unknown");
    toast(`以后总是提示：${shownKey(key)}`, () => { S.unknown.delete(key); save(); renderAll(); pushMark(key, "clear"); });
  }
  function restore(key) { S.known.delete(key); S.unknown.delete(key); save(); renderAll(); pushMark(key, "clear"); toast(`已恢复：${shownKey(key)}`); }

  // ------------------------------------------------- shared profile (local program) ---
  const API_H = { "X-Adaptive-Subs": "1", "Content-Type": "application/json" };
  async function loadShared() {
    try {
      const r = await fetch("api/profile", { headers: API_H, cache: "no-store" });
      const p = await r.json();
      if (!r.ok || !Array.isArray(p.known)) return;
      shared = p;
      S.level = nearestLevel(p.level);
      S.known = new Set(p.known);
      S.unknown = new Set(p.unknown);
      if (S.cues.length) renderAll(); else renderControls();
    } catch { /* old program version: keep this browser's own marks */ }
  }
  async function pushMark(key, state) {
    if (!shared) return;
    try {
      const r = await fetch("api/mark", { method: "POST", headers: API_H, body: JSON.stringify({ key, state }) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      loadShared();
    } catch { toast("标记没能保存到 Adaptive Subs 程序（只保存在这个浏览器里）"); }
  }
  let levelTimer = 0;
  function pushLevel() {
    if (!shared) return;
    clearTimeout(levelTimer);
    levelTimer = setTimeout(() => fetch("api/profile", { method: "POST", headers: API_H, body: JSON.stringify({ level: S.level }) })
      .then(() => loadShared()).catch(() => {}), 400);
  }

  function addToBook(entry) {
    if (bookKeys.has(entry.key)) return;
    S.book.push({ ...entry, vid: S.video.id, added: Date.now() });
    bookKeys = new Set(S.book.map((b) => b.key));
    if (entry.hasNote) { const mk = entry.mk || entry.key; S.unknown.add(mk); S.known.delete(mk); pushMark(mk, "unknown"); }
    save(); renderAll();
    toast(`已加入生词本：${entry.term}`);
  }
  function bookEntryFromCue(ci, noteIdx, word) {
    const c = S.cues[ci];
    if (noteIdx != null) {
      const n = c.notes[noteIdx];
      return { key: n.key, mk: markKey(n), term: n.key, zh: n.zh, kind: n.kind, lv: n.lv, sentence: c.text, t: c.s, hasNote: true };
    }
    // a word without a pre-generated gloss: use the same gloss if it appears elsewhere
    const key = word.toLowerCase();
    let found = null;
    S.cues.some((cc) => cc.notes.some((n) => (n.key === key ? (found = n) : false)));
    return { key, term: word, zh: found ? found.zh : "", kind: found ? found.kind : "word", lv: found ? found.lv : 0, sentence: c.text, t: c.s, hasNote: !!found };
  }

  function exportCSV() {
    const q = (s) => `"${String(s ?? "").replace(/"/g, '""')}"`;
    const rows = [["word", "meaning", "sentence", "time", "video"]].concat(
      S.book.map((b) => [b.term, b.zh, b.sentence, fmtTime(b.t), `https://www.youtube.com/watch?v=${b.vid}&t=${Math.floor(b.t)}s`]));
    const blob = new Blob(["﻿" + rows.map((r) => r.map(q).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "adaptive-subs-wordbook.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // --------------------------------------------------------------- toast ---
  // in real fullscreen only the fullscreen element is visible, so floating UI must live inside it
  function hostFor(el) {
    const host = document.fullscreenElement || document.body;
    if (el.parentElement !== host) host.appendChild(el);
  }

  let toastTimer = 0;
  function toast(msg, undo) {
    const el = $("#toast");
    hostFor(el);
    el.innerHTML = `<span>${esc(msg)}</span>${undo ? `<button type="button">撤销</button>` : ""}`;
    el.hidden = false;
    if (undo) $("button", el).onclick = () => { undo(); el.hidden = true; };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
  }

  // ----------------------------------------------------------- word card ---
  let cardWasPlaying = false, activeTok = null;

  function openCard(el) {
    closeCard(false);
    const ci = +el.dataset.c, c = S.cues[ci];
    const ni = el.dataset.n != null ? +el.dataset.n : null;
    const note = ni != null ? c.notes[ni] : null;
    const term = note ? c.text.slice(note.s, note.e) : el.dataset.w;
    const key = note ? markKey(note) : term.toLowerCase();
    const shown = note && S.visible[ci].has(ni);

    if (isPlaying()) { cardWasPlaying = true; player.pauseVideo(); }
    activeTok = el; el.classList.add("active-tok");

    let main;
    if (note) {
      const action = shown
        ? `<button class="btn btn-primary btn-sm" data-act="know" data-key="${esc(key)}">我认识了，不再提示</button>`
        : S.known.has(key)
          ? `<button class="btn btn-primary btn-sm" data-act="restore" data-key="${esc(key)}">恢复提示</button>`
          : `<button class="btn btn-primary btn-sm" data-act="unknown" data-key="${esc(key)}">标为生词，总是提示</button>`;
      const why = shown
        ? (S.unknown.has(key) ? "你标记过这个词，所以总是提示" : `按你的词汇量（≈ ${fmtNum(S.level)}）显示`)
        : S.known.has(key) ? (shared && note.kind === "word" ? "在你的熟词库里（考试词表 / 常见词 / 测试 / 你的标记），所以不提示" : "你标记过认识，所以不提示") : !isHard(note) ? `大约 ${fmtNum(note.lv)} 词水平，按你的词汇量和注释偏好不提示` : "这句已经有更难的词，或者这个词本集提示够多次了";
      main = `
        <div class="wc-head"><span class="wc-term">${esc(term)}</span>
          <span class="tag ${note.kind !== "word" ? `tag-${note.kind}` : ""}">${KIND_LABEL[note.kind]}</span>
          <span class="tag">≈ ${fmtNum(note.lv)} 词</span></div>
        <div class="wc-zh">${esc(note.zh)}</div>
        <div class="wc-note">${why}</div>
        <div class="wc-actions">${action}`;
    } else {
      main = `
        <div class="wc-head"><span class="wc-term">${esc(term)}</span></div>
        <div class="wc-none">这个词没有预先生成释义，一般是常用词。</div>
        <div class="wc-note">不确定的话，可以去词典查一下，或者先收进生词本。</div>
        <div class="wc-actions">`;
    }
    const inBook = bookKeys.has(key);
    main += `
        <button class="btn btn-ghost btn-sm" data-act="book" data-c="${ci}" ${ni != null ? `data-n="${ni}"` : ""} data-w="${esc(term)}" ${inBook ? "disabled" : ""}>${inBook ? "✓ 已在生词本" : "+ 生词本"}</button>
        <button class="btn btn-ghost btn-sm" data-act="replay" data-c="${ci}">重听本句</button>
        <a class="btn btn-ghost btn-sm" href="https://dict.youdao.com/result?word=${encodeURIComponent(term)}&lang=en" target="_blank" rel="noopener">词典 ↗</a>
      </div>`;

    const card = $("#wordCard");
    hostFor(card);
    card.innerHTML = `<button class="x-btn wc-close" data-act="close" aria-label="关闭">×</button>${main}`;
    card.hidden = false;
    // position (fixed) under the word, or above it when there is no room below
    const r = el.getBoundingClientRect(), cw = card.offsetWidth, ch = card.offsetHeight;
    let left = r.left + r.width / 2 - cw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - cw - 8));
    let top = r.bottom + 10;
    if (top + ch > window.innerHeight - 8) top = Math.max(8, r.top - ch - 10);
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
  }

  function closeCard(resume) {
    const card = $("#wordCard");
    if (card.hidden) return;
    card.hidden = true;
    if (activeTok) activeTok.classList.remove("active-tok");
    activeTok = null;
    if (resume && cardWasPlaying && player) player.playVideo();
    cardWasPlaying = false;
  }

  // -------------------------------------------------------------- player ---
  let player = null, ready = false, pausedFor = -1;

  function loadYouTube() {
    window.onYouTubeIframeAPIReady = () => {
      player = new YT.Player("yt", {
        videoId: S.video.id,
        // fs: 0 hides YouTube's fullscreen button: it would fullscreen the bare video without our subtitles
        playerVars: { cc_load_policy: 0, rel: 0, modestbranding: 1, playsinline: 1, fs: 0 },
        events: {
          // YouTube may switch its own captions on; keep only ours
          onReady: (e) => {
            ready = true;
            e.target.unloadModule("captions");
            // the viewer may have switched videos while the player was loading
            if (S.video && e.target.getVideoData().video_id !== S.video.id) e.target.cueVideoById(S.video.id);
          },
          onApiChange: (e) => e.target.unloadModule("captions"),
          onStateChange: (e) => { if (e.data === YT.PlayerState.PLAYING) e.target.unloadModule("captions"); },
        },
      });
    };
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(s);
  }

  const isPlaying = () => !!(ready && player.getPlayerState && player.getPlayerState() === 1);

  function cueAtOrBefore(t) {
    let lo = 0, hi = S.cues.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (S.cues[mid].s <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  let ytLoading = false;
  function ensurePlayer() {
    if (!S.video) return;
    if (ready) {
      if (player.getVideoData().video_id !== S.video.id) player.cueVideoById(S.video.id);
      return;
    }
    if (!ytLoading) { ytLoading = true; loadYouTube(); }   // its onReady picks up S.video
  }

  // while a video is generated live, tell the server where the viewer is, so it works there first
  let lastFocusT = -1, lastSentFocus = -1;
  function reportFocus(t) {
    if (!es || !S.video) { lastFocusT = t; return; }
    const jumped = lastFocusT >= 0 && Math.abs(t - lastFocusT) > 3;
    if (jumped || lastSentFocus < 0 || Math.abs(t - lastSentFocus) > 20) {
      lastSentFocus = t;
      fetch("api/focus", { method: "POST", headers: { "X-Adaptive-Subs": "1", "Content-Type": "application/json" },
        body: JSON.stringify({ v: S.video.id, t }) }).catch(() => {});
    }
    lastFocusT = t;
  }

  let lastIdx = -1;   // the line that was playing on the previous tick
  function tick() {
    if (!ready || !player.getCurrentTime) return;
    const t = player.getCurrentTime();
    reportFocus(t);
    let i = cueAtOrBefore(t);
    // intensive listening: pause when the line that was playing reaches its end.
    // Checked against the previous tick's line, so back-to-back lines can't slip past.
    if (S.autoPause && lastIdx >= 0 && pausedFor !== lastIdx && isPlaying()) {
      const end = S.cues[lastIdx].e;
      if (t >= end - 0.12 && t < end + 0.6) { player.pauseVideo(); pausedFor = lastIdx; i = lastIdx; }
    }
    lastIdx = i;
    // video resumed from YouTube's own controls while a word card is open: let subtitles move on
    if (!$("#wordCard").hidden && isPlaying()) closeCard(false);
    // a line stays on screen until it ends (+ a short grace), not until the next one starts
    const gap = i >= 0 && t > S.cues[i].e + GAP_GRACE;
    if ((i !== S.cur || gap !== S.gap) && $("#wordCard").hidden) {
      const moved = i !== S.cur;
      S.cur = i;
      S.gap = gap;
      renderStage();
      if (moved) markActiveRow();
    }
  }

  function seekCue(ci, play = true) {
    if (!S.cues[ci]) return;
    closeCard(false);
    pausedFor = -1;
    lastIdx = ci;
    S.cur = ci;
    S.gap = false;
    renderStage();
    markActiveRow(true);
    if (!ready) return;
    player.seekTo(S.cues[ci].s + 0.01, true);
    if (play) player.playVideo();
  }
  function seekTime(t) {
    const ci = cueAtOrBefore(t);
    seekCue(ci >= 0 ? ci : 0);
  }
  const replay = () => seekCue(S.cur >= 0 ? S.cur : 0);
  const step = (d) => seekCue(Math.max(0, Math.min(S.cues.length - 1, (S.cur >= 0 ? S.cur : -1) + d)));
  function togglePlay() {
    if (!ready) return;
    isPlaying() ? player.pauseVideo() : player.playVideo();
  }

  // ---- fullscreen: the frame (video + our subtitles + toolbar) goes fullscreen together.
  // Browsers without element fullscreen (iPhone Safari) get a CSS "fill the window" fallback.
  function toggleFullscreen() {
    const f = frame();
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    if (f.classList.contains("pseudo-fs")) { f.classList.remove("pseudo-fs"); onFullscreenChange(); return; }
    const req = f.requestFullscreen || f.webkitRequestFullscreen;
    if (req) {
      Promise.resolve(req.call(f)).catch(() => { f.classList.add("pseudo-fs"); onFullscreenChange(); });
    } else {
      f.classList.add("pseudo-fs");
      onFullscreenChange();
    }
  }
  function onFullscreenChange() {
    closeCard(false);
    hostFor($("#wordCard"));
    hostFor($("#toast"));
    const fs = isFs();
    $("#fsBtn span").textContent = fs ? "退出全屏" : "全屏";
    document.body.style.overflow = frame().classList.contains("pseudo-fs") ? "hidden" : "";
    placeSubs();
    renderStage();
  }

  // ---------------------------------------------------- vocabulary test ---
  let bank = null, T = null;
  const W = { 2: 1, 1: 0.3, 0: 0 };          // known / unsure / unknown
  const PAGE = 14;
  const bandLabel = (b) => (b.hi >= 1000 ? `${+(b.hi / 1000).toFixed(1)}k` : String(b.hi));

  // Opened by double-clicking the HTML file: browsers block fetch() on file:// and
  // YouTube refuses to play inside a local file, so explain how to open it properly.
  const IS_FILE = location.protocol === "file:";
  function showFileNotice() {
    $("#testBody").innerHTML = `
      <p class="eyebrow">打开方式不对</p>
      <h2>请通过本地服务器打开这个页面</h2>
      <p class="muted">现在的地址以 <code>file://</code> 开头，说明是直接双击打开的网页文件。浏览器出于安全限制，
      不允许这样的页面读取字幕和词汇测试数据，YouTube 视频也不能在本地文件里播放。</p>
      <ol class="intro-list">
        <li><b>最简单</b>：打开 Adaptive Subs 程序（AdaptiveSubs.exe，从源码运行则双击 <code>start.bat</code>），再访问 <code>http://127.0.0.1:8765/player.html</code></li>
        <li>或者直接用部署好的 GitHub Pages 链接</li>
      </ol>
      <div class="modal-actions"><button class="btn btn-primary" data-act="tclose">知道了</button></div>`;
    const dlg = $("#testDialog");
    if (!dlg.open) dlg.showModal();
  }

  async function openTest() {
    if (IS_FILE) { showFileNotice(); return; }
    if (shared) { if (isPlaying()) player.pauseVideo(); window.open("/profile", "_blank", "noopener"); return; }
    if (isPlaying()) player.pauseVideo();
    const dlg = $("#testDialog");
    if (!bank) {
      try {
        const r = await fetch("data/vocab_test.json");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        bank = await r.json();
        if (!bank.bands || !bank.bands.length) throw new Error("empty word bank");
      } catch (err) {
        bank = null;
        $("#testBody").innerHTML = `
          <p class="eyebrow">词汇测试</p>
          <h3>题库没有加载出来</h3>
          <p class="muted">读取 <code>data/vocab_test.json</code> 失败（${esc(err.message || err)}）。
          如果是部署到 GitHub Pages 的版本，请确认上传了 <code>site/data/</code> 里的 vocab_test.json；
          如果是直接双击打开的本地文件，请先打开 Adaptive Subs 程序，再通过 <code>http://127.0.0.1:8765/player.html</code> 访问。</p>
          <div class="modal-actions"><button class="btn btn-ghost" data-act="tclose">关闭</button><button class="btn btn-primary" data-act="tretry">重试</button></div>`;
        if (!dlg.open) dlg.showModal();
        return;
      }
    }
    T = newTest();
    renderTest();
    if (!dlg.open) dlg.showModal();
  }

  function newTest() {
    const words = [];
    bank.bands.forEach((b, bi) => sample(b.words, 6).forEach((w) => words.push({ type: "word", band: bi, word: w.word, data: w, ans: null })));
    const items = words.slice();
    // pseudo-words spread through the test, never in the first page's opening items
    sample(bank.nonwords, 6).forEach((nw) => {
      const at = 4 + Math.floor(Math.random() * (items.length - 3));
      items.splice(at, 0, { type: "non", word: nw, ans: null });
    });
    return { items, page: 0, stage: "intro", checks: [], result: null };
  }

  function renderTest() {
    const body = $("#testBody");
    if (T.stage === "intro") {
      body.innerHTML = `
        <p class="eyebrow">词汇测试 · 约 2 分钟</p>
        <h2>测测你认识多少英文单词</h2>
        <p class="muted">一共 ${T.items.length} 个词，从简单到难。能说出一个中文意思就选「认识」；眼熟但说不出来，选「不确定」。<br>
        里面混了几个<b>不存在的假词</b>，用来校正乱选；最后还会用几道例句题核对一下。</p>
        <div class="modal-actions"><button class="btn btn-ghost" data-act="tclose">以后再说</button><button class="btn btn-primary" data-act="tstart">开始测试</button></div>`;
      return;
    }
    if (T.stage === "words") {
      const pages = Math.ceil(T.items.length / PAGE);
      const slice = T.items.slice(T.page * PAGE, (T.page + 1) * PAGE);
      const answered = T.items.filter((x) => x.ans != null).length;
      const left = slice.filter((x) => x.ans == null).length;
      body.innerHTML = `
        <div class="test-head"><span>第 ${T.page + 1} / ${pages} 页</span><span>已选 ${answered} / ${T.items.length}</span></div>
        <div class="progress"><span style="width:${(answered / T.items.length) * 100}%"></span></div>
        <h3>看到这些词，能说出中文意思吗？</h3>
        <p class="muted">不用查词。只是眼熟、说不出意思，选「不确定」。</p>
        <div class="test-grid">${slice.map((x, k) => {
          const idx = T.page * PAGE + k;
          const btn = (v, label) => `<button type="button" data-act="tans" data-i="${idx}" data-v="${v}" aria-pressed="${x.ans === v}">${label}</button>`;
          const cls = x.ans != null ? " answered" : T.showMissing ? " missing" : "";
          return `<div class="titem${cls}"><span class="titem-word">${esc(x.word)}</span><span class="tchoices">${btn(2, "认识")}${btn(1, "不确定")}${btn(0, "不认识")}</span></div>`;
        }).join("")}</div>
        <div class="modal-actions">
          ${T.page > 0 ? `<button class="btn btn-ghost" data-act="tprev">上一页</button>` : `<button class="btn btn-ghost" data-act="tclose">退出</button>`}
          ${left ? `<button class="btn btn-ghost" data-act="trest">本页剩下的都选「不认识」</button>` : ""}
          <span class="spacer"></span>
          ${left && T.showMissing ? `<span class="missing-msg">还有 ${left} 个词没选，已用红框标出</span>` : left ? `<span class="muted" style="margin:0">本页还有 ${left} 个没选</span>` : ""}
          <button class="btn btn-primary" data-act="tnext">${T.page + 1 < pages ? "下一页" : "核对词义"}</button>
        </div>`;
      return;
    }
    if (T.stage === "check") {
      const done = T.checks.every((c) => c.pick != null);
      body.innerHTML = `
        <p class="eyebrow">最后一步</p>
        <h3>核对几个你选了「认识」的词</h3>
        <p class="muted">选出这个词在句子里的意思。不记得就选「不确定」。</p>
        ${T.checks.map((c, k) => `
          <div class="mcq${c.pick == null && T.showMissing ? " missing" : ""}">
            <div class="mcq-word">${esc(c.data.word)}</div>
            <div class="mcq-ex">${esc(c.data.example)}</div>
            <div class="mcq-opts">${c.opts.map((o) => `<button type="button" data-act="tpick" data-k="${k}" data-o="${esc(o)}" aria-pressed="${c.pick === o}">${esc(o)}</button>`).join("")}</div>
          </div>`).join("")}
        <div class="modal-actions">
          <button class="btn btn-ghost" data-act="tback">返回选词</button><span class="spacer"></span>
          ${!done && T.showMissing ? `<span class="missing-msg">还有题没选，已用红框标出</span>` : ""}
          <button class="btn btn-primary" data-act="tresult">查看结果</button>
        </div>`;
      return;
    }
    if (T.stage === "result") {
      const r = T.result, lv = nearestLevel(r.est);
      body.innerHTML = `
        <p class="eyebrow">测试结果</p>
        <div class="result-num">≈ ${fmtNum(r.est)}<small>词</small></div>
        <p class="muted">按本工具的词频表估算，用来决定字幕注释的多少，不代表你全部的英语水平。</p>
        <div class="bands">${r.bands.map((b) => `<div class="band" title="${Math.round(b.r * 100)}%"><div class="band-bar" style="height:${Math.max(2, b.r * 100)}%"></div></div>`).join("")}</div>
        <div class="band-labels">${bank.bands.map((b) => `<span>${bandLabel(b)}</span>`).join("")}</div>
        <div class="note-box">
          每根柱子是一个词频段里你大概认识的比例（越往右越难）。<br>
          假词：${r.nonYes ? `${r.nonTotal} 个假词里有 ${r.nonYes} 个选了「认识」，结果已经按这个往下校正` : `${r.nonTotal} 个假词都没有选「认识」，很好`}。
          ${r.checkTotal ? `<br>词义核对：答对 ${r.checkOk} / ${r.checkTotal} 题${r.checkOk < r.checkTotal ? "，答错的词频段已下调" : ""}。` : ""}
        </div>
        <div class="modal-actions">
          <button class="btn btn-ghost" data-act="tagain">再测一次</button><span class="spacer"></span>
          <button class="btn btn-primary" data-act="tapply" data-lv="${lv}">用这个结果设置字幕（≈ ${fmtNum(lv)} 词）</button>
        </div>`;
    }
  }

  function buildChecks() {
    const known = T.items.filter((x) => x.type === "word" && x.ans === 2);
    const picked = [];
    for (let b = bank.bands.length - 1; b >= 1 && picked.length < 4; b--) {
      const pool = known.filter((x) => x.band === b);
      if (pool.length) picked.push(sample(pool, 1)[0]);
    }
    return picked.map((x) => ({ band: x.band, data: x.data, opts: shuffle([x.data.zh, ...x.data.distractors]).concat("不确定"), pick: null }));
  }

  function scoreTest() {
    const non = T.items.filter((x) => x.type === "non");
    const fa = non.reduce((a, x) => a + W[x.ans], 0) / non.length;
    let checkOk = 0;
    const bands = bank.bands.map((b, bi) => {
      const its = T.items.filter((x) => x.type === "word" && x.band === bi);
      const p = its.reduce((a, x) => a + W[x.ans], 0) / its.length;
      let r = fa >= 0.95 ? 0 : Math.max(0, Math.min(1, (p - fa) / (1 - fa)));
      const ch = T.checks.filter((c) => c.band === bi);
      if (ch.length) {
        const ok = ch.filter((c) => c.pick === c.data.zh).length;
        checkOk += ok;
        r *= 0.5 + 0.5 * (ok / ch.length);
      }
      return { lo: b.lo, hi: b.hi, r };
    });
    const est = bands.reduce((a, b) => a + (b.hi - b.lo + 1) * b.r, 0);
    return {
      est: Math.max(500, Math.round(est / 100) * 100), bands,
      nonYes: non.filter((x) => x.ans === 2).length, nonTotal: non.length,
      checkOk, checkTotal: T.checks.length,
    };
  }

  function onTestClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    const dlg = $("#testDialog");
    const pageItems = () => (T ? T.items.slice(T.page * PAGE, (T.page + 1) * PAGE) : []);
    const showFirstMissing = () => { const m = $(".missing", dlg); if (m) m.scrollIntoView({ block: "center", behavior: "smooth" }); };
    if (act === "tclose") dlg.close();
    else if (act === "tretry") openTest();
    else if (act === "tstart") { T.stage = "words"; renderTest(); }
    else if (act === "tans") { T.items[+b.dataset.i].ans = +b.dataset.v; renderTest(); }
    else if (act === "trest") { pageItems().forEach((x) => { if (x.ans == null) x.ans = 0; }); renderTest(); }
    else if (act === "tprev") { T.page--; T.showMissing = false; renderTest(); }
    else if (act === "tnext") {
      if (pageItems().some((x) => x.ans == null)) { T.showMissing = true; renderTest(); showFirstMissing(); return; }
      T.showMissing = false;
      if ((T.page + 1) * PAGE < T.items.length) T.page++;
      else {
        T.checks = buildChecks();
        T.stage = T.checks.length ? "check" : "result";
        if (T.stage === "result") T.result = scoreTest();
      }
      renderTest();
      dlg.scrollTop = 0;
    }
    else if (act === "tpick") { T.checks[+b.dataset.k].pick = b.dataset.o; renderTest(); }
    else if (act === "tback") { T.stage = "words"; T.showMissing = false; renderTest(); }
    else if (act === "tresult") {
      if (T.checks.some((c) => c.pick == null)) { T.showMissing = true; renderTest(); showFirstMissing(); return; }
      T.result = scoreTest(); T.stage = "result"; T.showMissing = false; renderTest(); dlg.scrollTop = 0;
    }
    else if (act === "tagain") { T = newTest(); T.stage = "words"; renderTest(); }
    else if (act === "tapply") {
      S.level = +b.dataset.lv;
      S.test = { est: T.result.est, date: new Date().toISOString().slice(0, 10) };
      save(); renderAll();
      $("#testDialog").close();
      toast(`已按测试结果设置：≈ ${fmtNum(S.level)} 词`);
    }
  }

  // ------------------------------------------------------------- events ---
  function onAction(e) {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const act = b.dataset.act, key = b.dataset.key;
    if (act === "know") { closeCard(true); markKnown(key); }
    else if (act === "unknown") { closeCard(true); markUnknown(key); }
    else if (act === "restore") { closeCard(true); restore(key); }
    else if (act === "book") {
      const ci = +b.dataset.c;
      if (b.dataset.n != null || b.dataset.w) addToBook(bookEntryFromCue(ci, b.dataset.n != null ? +b.dataset.n : null, b.dataset.w || ""));
      else {
        // from the pre-watch list: find the note with this key in that cue
        const ni = S.cues[ci].notes.findIndex((n) => n.key === key);
        addToBook(bookEntryFromCue(ci, ni >= 0 ? ni : null, key));
      }
      closeCard(true);
    }
    else if (act === "unbook") {
      const [removed] = S.book.splice(+b.dataset.i, 1);
      bookKeys = new Set(S.book.map((x) => x.key));
      save(); renderAll();
      toast(`已从生词本删除：${removed.term}`, () => { S.book.push(removed); bookKeys = new Set(S.book.map((x) => x.key)); save(); renderAll(); });
    }
    else if (act === "replay") seekCue(+b.dataset.c);
    else if (act === "seek") seekCue(+b.dataset.c);
    else if (act === "seekt") seekTime(+b.dataset.t);
    else if (act === "export") exportCSV();
    else if (act === "close") closeCard(true);
  }

  function bindUI() {
    $("#density").addEventListener("click", async (e) => {
      const button = e.target.closest("[data-generation]");
      if (!button) return;
      if (button.dataset.generation === "retry") return openVideo(S.retryId);
      button.disabled = true;
      try {
        const r = await fetch("api/jobs/cancel", { method: "POST",
          headers: { "X-Adaptive-Subs": "1", "Content-Type": "application/json" },
          body: JSON.stringify({ id: S.video.id }) });
        const data = await r.json();
        toast(data.error || "正在停止，已经发出的请求会先完成");
      } catch { toast("连不上本地程序"); }
      finally { button.disabled = false; }
    });
    $("#line").addEventListener("click", (e) => { const t = e.target.closest(".tok"); if (t) openCard(t); });
    $("#urlForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("#urlInput").value;
      $("#urlInput").value = "";
      $("#urlInput").blur();
      openVideo(v);
    });
    $("#videoSelect").addEventListener("change", (e) => openVideo(e.target.value));
    $("#posSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.subPos = b.dataset.pos; save(); renderControls(); renderStage(); } });
    $("#fsBtn").addEventListener("click", toggleFullscreen);
    $("#fsBar").addEventListener("click", (e) => {
      const b = e.target.closest("[data-fs]");
      if (!b) return;
      ({ prev: () => step(-1), next: () => step(1), replay, exit: toggleFullscreen })[b.dataset.fs]();
    });
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("webkitfullscreenchange", onFullscreenChange);
    window.addEventListener("scroll", () => closeCard(false), { passive: true });
    $("#wordCard").addEventListener("click", onAction);
    $("#tab-preview").addEventListener("click", onAction);
    $("#tab-book").addEventListener("click", onAction);
    $("#tab-transcript").addEventListener("click", (e) => { const r = e.target.closest(".tr-row"); if (r) seekCue(+r.dataset.c); });
    ["wheel", "touchmove"].forEach((ev) => $("#tab-transcript").addEventListener(ev, () => { lastUserScroll = Date.now(); }, { passive: true }));

    $("#levelRange").addEventListener("input", (e) => { S.level = LEVELS[+e.target.value]; save(); renderAll(); pushLevel(); });
    $("#modeSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.mode = b.dataset.mode; save(); renderAll(); } });
    $("#glossSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.gloss = b.dataset.gloss; save(); renderAll(); } });
    $("#prefSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.pref = b.dataset.pref; save(); renderAll(); } });
    $("#autoPause").addEventListener("change", (e) => { S.autoPause = e.target.checked; pausedFor = -1; save(); toast(S.autoPause ? "每句结束时会自动暂停，按空格继续" : "已关闭每句暂停"); });
    $$(".tabs button").forEach((b) => b.addEventListener("click", () => { S.tab = b.dataset.tab; save(); renderControls(); if (S.tab === "transcript") markActiveRow(true); }));

    $("#prevLine").addEventListener("click", () => step(-1));
    $("#nextLine").addEventListener("click", () => step(1));
    $("#replayLine").addEventListener("click", replay);

    $("#openTest").addEventListener("click", openTest);
    $("#levelTest").addEventListener("click", openTest);
    $("#testBody").addEventListener("click", onTestClick);
    $("#openIntro").addEventListener("click", () => $("#introDialog").showModal());
    $("#introTest").addEventListener("click", () => { $("#introDialog").close(); openTest(); });
    $("#introSkip").addEventListener("click", () => $("#introDialog").close());
    $("#introDialog").addEventListener("close", () => { S.introSeen = true; save(); });
    ["introDialog", "testDialog"].forEach((id) => $("#" + id).addEventListener("click", (e) => { if (e.target.id === id) e.target.close(); }));

    document.addEventListener("pointerdown", (e) => {
      const card = $("#wordCard");
      if (!card.hidden && !card.contains(e.target) && !e.target.closest(".tok")) closeCard(true);
    });
    document.addEventListener("keydown", (e) => {
      if (document.querySelector("dialog[open]")) return;
      if (e.key === "Escape") {
        if (!$("#wordCard").hidden) closeCard(true);
        else if (frame().classList.contains("pseudo-fs")) toggleFullscreen();
        return;
      }
      if (e.target.closest("input, textarea, select") || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === " " && !e.target.closest("button, a")) { e.preventDefault(); togglePlay(); }
      else if (e.key === "f" || e.key === "F") toggleFullscreen();
      else if (e.key === "r" || e.key === "R") replay();
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
    });
    window.addEventListener("resize", () => closeCard(false));
    // re-decide inside/below whenever the video's own width changes (rotation, window resize, layout)
    const relayout = () => {
      const before = $("#stageText").parentElement;
      placeSubs();
      if ($("#stageText").parentElement !== before) renderStage();
    };
    if (window.ResizeObserver) new ResizeObserver(relayout).observe(frame());
    window.addEventListener("resize", () => setTimeout(relayout, 60));   // backup for browsers/views without RO
  }

  // ------------------------------------------------------- video loading ---
  // Saved videos load from data/<id>.json. With the local server (server.py) running, any YouTube
  // link can be opened: subtitles arrive in seconds, annotations stream in while you watch.
  let index = [];      // saved videos [{id, title, file}]
  let live = false;    // local server available
  let es = null;       // EventSource of a video being generated

  async function loadIndex() {
    try {
      const r = await fetch("data/index.json", { cache: "no-store" });
      index = r.ok ? await r.json() : [];
    } catch { index = []; }
    if (!Array.isArray(index)) index = [];
  }

  function resetVideo(video) {
    if (es) { es.close(); es = null; }
    closeCard(false);
    S.video = video;
    S.cues = [];
    S.cur = -1;
    S.gap = false;
    S.annDone = null;
    S.progress = null;
    S.statusMsg = "";
    S.retryId = null;
    lastIdx = -1; pausedFor = -1; lastFocusT = -1; lastSentFocus = -1;
  }

  function renderVideoSelect() {
    const list = index.slice();
    if (S.video && !list.some((v) => v.id === S.video.id)) list.push({ id: S.video.id, title: S.video.title || S.video.id, pending: true });
    const sel = $("#videoSelect");
    sel.innerHTML = list.map((v) => `<option value="${esc(v.id)}">${esc(v.title)}${v.pending ? "（生成中）" : ""}</option>`).join("");
    if (S.video) sel.value = S.video.id;
    sel.hidden = !list.length;
  }

  function afterVideoChange() {
    history.replaceState(null, "", `?v=${encodeURIComponent(S.video.id)}`);
    renderVideoSelect();
    renderAll();
    ensurePlayer();
  }

  async function loadSaved(entry) {
    let data;
    try { data = await (await fetch("data/" + entry.file, { cache: "no-store" })).json(); }
    catch { toast(`字幕文件 data/${entry.file} 读取失败`); return; }
    resetVideo(data.video);
    S.cues = data.cues;
    afterVideoChange();
  }

  async function openVideo(input) {
    const v = String(input || "").trim();
    if (!v) return;
    const saved = index.find((x) => x.id === v);
    if (saved) return loadSaved(saved);
    if (!live) { toast("打开新视频需要先打开 Adaptive Subs 程序"); return; }
    let r;
    try { r = await (await fetch("api/open", { method: "POST",
      headers: { "X-Adaptive-Subs": "1", "Content-Type": "application/json" }, body: JSON.stringify({ url: v }) })).json(); }
    catch { toast("连不上本地服务器，请确认 Adaptive Subs 程序还开着"); return; }
    if (r.error) { toast(r.error); return; }
    if (r.cached) {
      await loadIndex();
      return loadSaved(index.find((x) => x.id === r.id) || { id: r.id, file: r.file });
    }
    startLive(r.id);
  }

  function startLive(id) {
    resetVideo({ id, title: "正在获取字幕…" });
    S.annDone = new Set();
    S.statusMsg = "正在获取英文字幕…";
    afterVideoChange();
    let queued = false;
    const rerender = () => {   // batch re-renders while annotations stream in
      if (queued) return;
      queued = true;
      setTimeout(() => { queued = false; renderAll(false); }, 400);
    };
    const src = new AdaptiveEvents(id);
    es = src;
    const on = (type, fn) => src.addEventListener(type, (e) => { if (es === src) fn(JSON.parse(e.data)); });
    on("status", (d) => { S.statusMsg = d.msg; renderStage(); });
    on("meta", (d) => {
      S.video = d.video;
      S.cues = d.cues.map((c) => ({ s: c.s, e: c.e, text: c.text, zh: "", notes: [] }));
      renderVideoSelect();
      renderAll();
      if (d.source === "auto") toast("这个视频没有人工字幕，用的是 YouTube 自动字幕，可能有错字或断句不准");
    });
    on("ann", (d) => {
      for (const [k, notes] of Object.entries(d)) if (S.cues[+k]) { S.cues[+k].notes = notes; S.annDone.add(+k); }
      rerender();
    });
    on("zh", (d) => { for (const [k, z] of Object.entries(d)) if (S.cues[+k]) S.cues[+k].zh = z; rerender(); });
    on("progress", (d) => { S.progress = d; renderDensity(); });
    on("done", async (d) => {
      src.close(); es = null;
      S.annDone = null;
      S.progress = { done: 1, total: 1, finished: true, seconds: d.seconds };
      await loadIndex();
      renderVideoSelect();
      renderAll(false);
      toast(`全部注释已生成（用时 ${d.seconds} 秒），以后打开这个视频会直接加载`);
    });
    on("fail", (d) => {
      src.close(); es = null;
      S.annDone = null; S.progress = null;
      S.statusMsg = d.msg;
      S.retryId = d.retry ? id : null;
      renderAll(false);
      toast(d.msg);
    });
    src.onerror = () => {
      if (es === src && src.readyState === 2) {
        es = null; S.progress = null; S.retryId = id;
        renderDensity();
        toast("连接断开。重新打开本地程序后，可以继续处理缺失字幕。");
      }
    };
  }

  // --------------------------------------------------------------- init ---
  async function init() {
    bindUI();
    renderControls();
    renderStage();
    if (IS_FILE) {
      $("#line").innerHTML = `<span class="placeholder">请通过本地服务器打开：先打开 Adaptive Subs 程序</span>`;
      showFileNotice();
      return;
    }
    try { live = !!(await (await fetch("api/health", { cache: "no-store" })).json()).ok; } catch { live = false; }
    $("#urlForm").hidden = !live;
    $("#settingsLink").hidden = !live;
    if (live) {
      await loadShared();
      // the profile page (another tab) may change it: reload when coming back
      document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && shared) loadShared(); });
    }
    // a local address without the live server = an old plain file server: say how to fix it
    // (on GitHub Pages there is never a server, so no hint there)
    $("#liveHint").hidden = live || !/^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
    await loadIndex();
    renderVideoSelect();
    setInterval(tick, 100);
    const want = params.get("v");
    if (want && (live || index.some((v) => v.id === want))) await openVideo(want);
    else if (index.length) await loadSaved(index[0]);
    else {
      S.statusMsg = live ? "在上方粘贴一个 YouTube 链接开始" : "没有找到字幕数据（data/index.json）：先运行 annotate.py，或确认 data 文件夹已上传";
      renderStage();
    }
    if (!S.introSeen) $("#introDialog").showModal();
  }

  init();
})();
