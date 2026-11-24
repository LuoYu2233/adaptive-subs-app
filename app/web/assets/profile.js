/* Profile page: identity, exam scores, adaptive vocabulary test. Saves to the local program
   (/api/profile), which the browser extension and the player read from. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const H = { "X-Adaptive-Subs": "1", "Content-Type": "application/json" };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n).toLocaleString("en-US");
  const shuffle = (a) => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };

  const IDENTITIES = [["high", "高中生"], ["undergrad", "大学生"], ["grad", "研究生"], ["work", "已工作"], ["other", "其他"]];
  const EXAMS = [["gaokao", "高考英语", 150, 1], ["cet4", "四级", 710, 1], ["cet6", "六级", 710, 1],
                 ["kaoyan", "考研英语", 100, 1], ["ielts", "雅思", 9, 0.5], ["toefl", "托福", 120, 1]];
  let P = null;          // server payload
  let identity = null;

  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: H, body: JSON.stringify(body) } : { headers: H });
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  // ------------------------------------------------------------ steps 1–2 ---
  function renderForm() {
    const prof = (P && P.profile) || {};
    identity = prof.identity || identity;
    $("#identity").innerHTML = IDENTITIES.map(([id, name]) =>
      `<button type="button" class="chip" data-id="${id}" aria-pressed="${identity === id}">${name}</button>`).join("");
    $("#field").value = prof.field || "";
    const ex = prof.exams || {};
    $("#exams").innerHTML = EXAMS.map(([id, name, max, step]) => `
      <div><label class="k" for="ex-${id}">${name}</label>
      <div class="exam-in"><input type="number" id="ex-${id}" min="0" max="${max}" step="${step}" inputmode="decimal"
        value="${ex[id] != null ? ex[id] : ""}" placeholder="没考过"><span>/ ${max}</span></div></div>`).join("");
  }

  $("#identity").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (!b) return;
    identity = b.dataset.id;
    document.querySelectorAll("#identity .chip").forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
  });

  $("#saveProfile").addEventListener("click", async () => {
    const msg = $("#msg1"), btn = $("#saveProfile");
    const exams = {};
    for (const [id] of EXAMS) { const v = $(`#ex-${id}`).value.trim(); if (v !== "") exams[id] = v; }
    if (!identity && !Object.keys(exams).length) {
      msg.className = "msg bad"; msg.textContent = "至少选一下你现在的身份"; return;
    }
    btn.disabled = true; msg.className = "msg"; msg.textContent = "正在保存…";
    try {
      P = await api("/api/profile", { identity, field: $("#field").value, exams });
      msg.className = "msg ok";
      msg.textContent = P.lists.length
        ? `已保存。按你的成绩，${P.lists.join("、")}词表算作已认识（共约 ${fmt(P.stats.exams)} 词），词汇量先估计为 ≈ ${fmt(P.prior)}`
        : `已保存。词汇量先估计为 ≈ ${fmt(P.prior)}`;
      renderSummary();
      if (!T || T.stage === "intro" || T.stage === "result") { T = null; renderTest(); }
      $("#s3").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      msg.className = "msg bad"; msg.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  });

  // -------------------------------------------------------------- summary ---
  function renderSummary() {
    const box = $("#summary");
    if (!P || !P.setup) { box.hidden = true; return; }
    box.hidden = false;
    const ident = (IDENTITIES.find(([id]) => id === P.profile.identity) || [0, ""])[1];
    const exams = Object.entries(P.profile.exams || {}).map(([k, v]) => `${(EXAMS.find((e) => e[0] === k) || [0, k])[1]} ${v}`);
    box.innerHTML = `
      <h2>现在的画像</h2>
      <div class="summary">
        <div class="big">≈ ${fmt(P.level)}<small>词</small></div>
        <div class="facts">
          <div>${[ident, P.profile.field].filter(Boolean).map(esc).join("，") || "身份未填"}${exams.length ? "，" + esc(exams.join("、")) : ""}</div>
          <div>词汇量来自：<b>${P.test ? `词汇测试（${esc(P.test.date)}）` : "考试成绩 / 身份推测，还没做测试"}</b></div>
          <div>熟词库：<b>${fmt(P.stats.known)}</b> 词${P.lists.length ? `（${esc(P.lists.join("、"))}词表 ${fmt(P.stats.exams)} 词` : "（"} + 最常见的 ${fmt(P.stats.freq_cut)} 个词，有重叠）</div>
          <div>看视频时标记：认识 <b>${P.stats.marked_known}</b> 个，不认识 <b>${P.stats.marked_unknown}</b> 个</div>
        </div>
      </div>`;
    renderMarks();
  }

  function renderMarks() {
    const box = $("#s4");
    const m = P.marks, n = m.known.length + m.unknown.length;
    box.hidden = !n;
    if (!n) return;
    const chip = (k) => {
      const detail = (P.mark_details || {})[k];
      const text = detail && detail.zh ? `${detail.term || AdaptiveLearning.label(k)} · ${detail.zh}` : AdaptiveLearning.label(k);
      return `<span class="mk">${esc(text)}${/^ctx:/.test(k) ? '<small class="muted">（当前意思）</small>' : /^sense:/.test(k) ? '<small class="muted">（旧版用法标记）</small>' : ""}<button type="button" data-clear="${esc(k)}" aria-label="恢复 ${esc(text)}">×</button></span>`;
    };
    $("#marks").innerHTML = `
      ${m.unknown.length ? `<div class="muted">我不认识（总是注释）</div><div class="marks">${m.unknown.map((k) => chip(k)).join("")}</div>` : ""}
      ${m.known.length ? `<div class="muted" style="margin-top:12px">我认识（不再注释）</div><div class="marks">${m.known.map((k) => chip(k)).join("")}</div>` : ""}
      <div class="actions"><button class="btn" type="button" id="resetMarks">清空全部标记</button></div>`;
  }

  $("#s4").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-clear]");
    try {
      if (b) { await api("/api/mark", { key: b.dataset.clear, state: "clear" }); }
      else if (e.target.id === "resetMarks") { await api("/api/profile", { reset_marks: true }); }
      else return;
      P = await api("/api/profile");
      renderSummary();
    } catch (err) { alert(err.message); }
  });

  // ---------------------------------------------------- adaptive test -----
  // Stage A: every frequency band, fewer words in bands well below the exam-based guess.
  // Stage B: extra words in the 2–3 bands where "known" turns into "unknown" (that's where accuracy matters).
  // Stage C: meaning check on words answered "认识" (multiple choice in a sentence) to catch over-claiming.
  // Score: pseudo-word false alarms and failed checks pull each band down; bands are then smoothed
  // so a harder band never scores above an easier one.
  const W = { 2: 1, 1: 0.3, 0: 0 };
  const PAGE = 12;
  let bank = null, T = null;

  async function loadBank() {
    if (bank) return bank;
    const r = await fetch("/data/vocab_test.json");
    if (!r.ok) throw new Error(`题库读取失败（HTTP ${r.status}）`);
    bank = await r.json();
    bank.bands.forEach((b, i) => { b.i = i; b.pool = shuffle(b.words); });
    return bank;
  }
  const bandLabel = (b) => (b.hi >= 1000 ? `${+(b.hi / 1000).toFixed(1)}k` : String(b.hi));

  function takeWords(b, n) { return b.pool.splice(0, n).map((w) => ({ type: "word", band: b.i, word: w.word, data: w, ans: null })); }
  function takeNon(n) { return T.non.splice(0, n).map((w) => ({ type: "non", word: w, ans: null })); }
  function sprinkle(items, extra, from) {
    extra.forEach((x) => items.splice(from + Math.floor(Math.random() * (items.length - from + 1)), 0, x));
    return items;
  }

  function newTest() {
    bank.bands.forEach((b) => { b.pool = shuffle(b.words); });
    const prior = (P && P.prior) || 3000;
    T = { stage: "intro", page: 0, non: shuffle(bank.nonwords), items: [], checks: [], prior };
    const items = [];
    for (const b of bank.bands) items.push(...takeWords(b, b.hi <= prior * 0.5 ? 3 : 5));
    T.items = sprinkle(items, takeNon(4), 3);
    T.stageAEnd = T.items.length;
  }

  function bandRatios(items) {
    const non = items.filter((x) => x.type === "non" && x.ans != null);
    const fa = non.length ? non.reduce((a, x) => a + W[x.ans], 0) / non.length : 0;
    return bank.bands.map((b) => {
      const its = items.filter((x) => x.type === "word" && x.band === b.i && x.ans != null);
      const p = its.length ? its.reduce((a, x) => a + W[x.ans], 0) / its.length : 0;
      return { n: its.length, r: fa >= 0.95 ? 0 : Math.max(0, Math.min(1, (p - fa) / (1 - fa))), fa };
    });
  }

  function addStageB() {
    const rs = bandRatios(T.items);
    let focus = rs.map((x, i) => i).filter((i) => rs[i].r >= 0.2 && rs[i].r <= 0.85);
    if (!focus.length) {                                 // a sharp edge: the bands on either side of it
      const k = rs.findIndex((x) => x.r < 0.5);
      focus = k <= 0 ? [Math.max(0, k)] : [k - 1, k];
    }
    if (focus.length === 1) {                            // also its neighbour on the other side of 50 %
      const i = focus[0], j = rs[i].r >= 0.5 ? i + 1 : i - 1;
      if (j >= 0 && j < rs.length) focus = [Math.min(i, j), Math.max(i, j)];
    }
    if (focus.length > 3) {                              // keep the three nearest to the 50 % point
      focus.sort((a, b) => Math.abs(rs[a].r - 0.5) - Math.abs(rs[b].r - 0.5));
      focus = focus.slice(0, 3).sort((a, b) => a - b);
    }
    const extra = [];
    focus.forEach((i) => extra.push(...takeWords(bank.bands[i], 6)));
    T.focus = focus;
    T.items.push(...sprinkle(shuffle(extra), takeNon(2), 1));
  }

  function buildChecks() {
    const known = T.items.filter((x) => x.type === "word" && x.ans === 2);
    const order = [...new Set([...(T.focus || []).slice().reverse(), ...bank.bands.map((b) => b.i).reverse()])];
    const picked = [];
    for (const bi of order) {
      const pool = shuffle(known.filter((x) => x.band === bi && !picked.includes(x)));
      picked.push(...pool.slice(0, (T.focus || []).includes(bi) ? 2 : 1));
      if (picked.length >= 6) break;
    }
    return picked.slice(0, 6).map((x) => ({ item: x, band: x.band, data: x.data, opts: shuffle([x.data.zh, ...x.data.distractors]).concat("不确定"), pick: null }));
  }

  function score() {
    const rs = bandRatios(T.items);
    const allOk = T.checks.filter((c) => c.pick === c.data.zh).length;
    const accAll = T.checks.length ? allOk / T.checks.length : 1;
    const lowest = Math.min(...T.checks.map((c) => c.band), 99);
    rs.forEach((x, i) => {
      const ch = T.checks.filter((c) => c.band === i);
      if (ch.length) x.r *= 0.4 + 0.6 * (ch.filter((c) => c.pick === c.data.zh).length / ch.length);
      else if (i > lowest) x.r *= 0.4 + 0.6 * accAll;     // untested harder bands: overall accuracy
    });
    // smoothing (pool adjacent violators): a harder band never scores above an easier one
    const blocks = rs.map((x) => ({ r: x.r, w: Math.max(1, x.n), len: 1 }));
    for (let i = 0; i < blocks.length - 1;) {
      if (blocks[i].r < blocks[i + 1].r) {
        const a = blocks[i], b = blocks[i + 1];
        blocks.splice(i, 2, { r: (a.r * a.w + b.r * b.w) / (a.w + b.w), w: a.w + b.w, len: a.len + b.len });
        if (i > 0) i--;
      } else i++;
    }
    const smooth = blocks.flatMap((b) => Array(b.len).fill(b.r));
    const est = bank.bands.reduce((a, b, i) => a + (b.hi - b.lo + 1) * smooth[i], 0);
    const failed = new Set(T.checks.filter((c) => c.pick !== c.data.zh).map((c) => c.item.word));
    return {
      est: Math.max(500, Math.round(est / 100) * 100), bands: smooth,
      fa: rs[0].fa, nonYes: T.items.filter((x) => x.type === "non" && x.ans === 2).length,
      nonTotal: T.items.filter((x) => x.type === "non").length,
      checkOk: allOk, checkTotal: T.checks.length,
      known: T.items.filter((x) => x.type === "word" && x.ans === 2 && !failed.has(x.word)).map((x) => x.word),
      unknown: T.items.filter((x) => x.type === "word" && (x.ans === 0 || failed.has(x.word))).map((x) => x.word),
    };
  }

  async function renderTest() {
    const box = $("#test");
    if (!T) {
      try { await loadBank(); } catch (e) { box.innerHTML = `<p class="msg bad">${esc(e.message)}</p>`; return; }
      newTest();
    }
    if (T.stage === "intro") {
      box.innerHTML = `
        <p class="sub">约 3 分钟。先每个难度抽几个词，再在你的水平分界附近多问几个，最后用例句核对几个词义。<br>
        能说出一个中文意思就选「认识」；眼熟但说不出来选「不确定」。里面混了几个<b>不存在的假词</b>，用来校正乱选。</p>
        ${P && P.test ? `<p class="muted" style="font-size:13px">上次测试：≈ ${fmt(P.test.est)} 词（${esc(P.test.date)}）。再测一次会覆盖上次的结果。</p>` : ""}
        <div class="actions"><button class="btn primary" type="button" data-act="start">${P && P.test ? "重新测试" : "开始测试"}</button></div>`;
      return;
    }
    if (T.stage === "words") {
      const pages = Math.ceil(T.items.length / PAGE);
      const slice = T.items.slice(T.page * PAGE, (T.page + 1) * PAGE);
      const answered = T.items.filter((x) => x.ans != null).length;
      const left = slice.filter((x) => x.ans == null).length;
      box.innerHTML = `
        <div class="test-head"><span>${T.stageB ? "第二轮：在你的水平附近多问几个" : "第一轮"}，第 ${T.page + 1} / ${pages} 页</span><span>${answered} / ${T.items.length}</span></div>
        <div class="progress"><span style="width:${(answered / T.items.length) * 100}%"></span></div>
        <div class="titems">${slice.map((x, k) => {
          const idx = T.page * PAGE + k;
          const btn = (v, label) => `<button type="button" data-act="ans" data-i="${idx}" data-v="${v}" aria-pressed="${x.ans === v}">${label}</button>`;
          return `<div class="titem${x.ans != null ? " answered" : T.showMissing ? " missing" : ""}"><span class="tw">${esc(x.word)}</span><span class="tc">${btn(2, "认识")}${btn(1, "不确定")}${btn(0, "不认识")}</span></div>`;
        }).join("")}</div>
        <div class="actions">
          ${T.page > 0 ? `<button class="btn" type="button" data-act="prev">上一页</button>` : ""}
          ${left ? `<button class="btn" type="button" data-act="rest">本页剩下的都选「不认识」</button>` : ""}
          <span class="spacer"></span>
          ${left && T.showMissing ? `<span class="msg bad">还有 ${left} 个没选（红框）</span>` : ""}
          <button class="btn primary" type="button" data-act="next">下一页</button>
        </div>`;
      return;
    }
    if (T.stage === "check") {
      box.innerHTML = `
        <p class="sub"><b>最后一步：</b>选出这些词在句子里的意思（都是你选了「认识」的词）。不记得就选「不确定」。</p>
        ${T.checks.map((c, k) => `
          <div class="mcq${c.pick == null && T.showMissing ? " missing" : ""}">
            <div class="mcq-word">${esc(c.data.word)}</div>
            <div class="mcq-ex">${esc(c.data.example)}</div>
            <div class="mcq-opts">${c.opts.map((o) => `<button type="button" data-act="pick" data-k="${k}" data-o="${esc(o)}" aria-pressed="${c.pick === o}">${esc(o)}</button>`).join("")}</div>
          </div>`).join("")}
        <div class="actions"><span class="spacer"></span>
          ${T.showMissing && T.checks.some((c) => c.pick == null) ? `<span class="msg bad">还有题没选（红框）</span>` : ""}
          <button class="btn primary" type="button" data-act="finish">查看结果</button></div>`;
      return;
    }
    if (T.stage === "result") {
      const r = T.result;
      box.innerHTML = `
        <div class="big">≈ ${fmt(r.est)}<small>词</small></div>
        <p class="muted" style="margin:4px 0 0">${P && P.prior ? `按考试成绩 / 身份的估计是 ≈ ${fmt(P.prior)}。` : ""}结果已保存，字幕从现在起按它来判断。</p>
        <div class="bands">${r.bands.map((x) => `<div class="band" title="${Math.round(x * 100)}%"><div class="band-bar" style="height:${Math.max(2, x * 100)}%"></div></div>`).join("")}</div>
        <div class="band-labels">${bank.bands.map((b) => `<span>${bandLabel(b)}</span>`).join("")}</div>
        <div class="note">每根柱子是一个词频段里你大概认识的比例，越往右越难。<br>
          假词：${r.nonYes ? `${r.nonTotal} 个里有 ${r.nonYes} 个选了「认识」，结果已经往下校正` : `${r.nonTotal} 个都没选「认识」`}。
          ${r.checkTotal ? `词义核对：答对 ${r.checkOk} / ${r.checkTotal}${r.checkOk < r.checkTotal ? "，答错的词频段已下调，答错的词会继续注释" : ""}。` : ""}<br>
          你选了「认识」的 ${r.known.length} 个词加入熟词库，选了「不认识」的 ${r.unknown.length} 个词以后总会注释。</div>
        <div class="actions"><button class="btn" type="button" data-act="again">再测一次</button></div>`;
    }
  }

  $("#test").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    const pageItems = () => T.items.slice(T.page * PAGE, (T.page + 1) * PAGE);
    const top = () => $("#s3").scrollIntoView({ behavior: "smooth", block: "start" });
    if (act === "start") { T.stage = "words"; renderTest(); }
    else if (act === "ans") { T.items[+b.dataset.i].ans = +b.dataset.v; renderTest(); }
    else if (act === "rest") { pageItems().forEach((x) => { if (x.ans == null) x.ans = 0; }); renderTest(); }
    else if (act === "prev") { T.page--; T.showMissing = false; renderTest(); }
    else if (act === "next") {
      if (pageItems().some((x) => x.ans == null)) { T.showMissing = true; renderTest(); return; }
      T.showMissing = false;
      if ((T.page + 1) * PAGE < T.items.length) T.page++;
      else if (!T.stageB) { T.stageB = true; addStageB(); T.page = Math.floor(T.stageAEnd / PAGE); if (T.page * PAGE >= T.items.length) T.page--; }
      else { T.checks = buildChecks(); T.stage = T.checks.length ? "check" : "save"; }
      if (T.stage === "save") return finish();
      renderTest(); top();
    }
    else if (act === "pick") { T.checks[+b.dataset.k].pick = b.dataset.o; renderTest(); }
    else if (act === "finish") {
      if (T.checks.some((c) => c.pick == null)) { T.showMissing = true; renderTest(); return; }
      finish();
    }
    else if (act === "again") { T = null; renderTest(); }
  });

  async function finish() {
    T.result = score();
    T.stage = "result";
    try {
      P = await api("/api/profile", { test: { est: T.result.est, bands: T.result.bands, known: T.result.known, unknown: T.result.unknown } });
      renderSummary();
    } catch (e) {
      T.saveError = e.message;
    }
    await renderTest();
    if (T.saveError) $("#test").insertAdjacentHTML("afterbegin", `<p class="msg bad">保存失败：${esc(T.saveError)}</p>`);
    $("#s3").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ boot ---
  (async () => {
    try {
      P = await api("/api/profile");
    } catch (e) {
      $("#msg1").className = "msg bad";
      $("#msg1").textContent = "连不上本地程序：请确认 Adaptive Subs 的窗口还开着，然后刷新本页";
    }
    renderForm();
    renderSummary();
    renderTest();
  })();
})();
