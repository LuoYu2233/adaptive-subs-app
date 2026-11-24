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
  let recoveryFingerprint = null;
  let profileReady = false, profileLoading = false;

  function setProfileReady(ready) {
    profileReady = ready;
    for (const id of ["s1", "s2", "s3"]) $("#" + id).hidden = !ready;
    for (const id of ["saveProfile", "chooseBackup", "clearProfile"]) $("#" + id).disabled = !ready;
    if (ready) $("#profileConnection").hidden = true;
  }

  function validProfile(p) {
    const count = n => Number.isSafeInteger(n) && n >= 0;
    return p && count(p.rev) && Number.isFinite(p.level) && Number.isFinite(p.prior)
      && typeof p.setup === "boolean" && p.profile && typeof p.profile === "object" && !Array.isArray(p.profile)
      && p.stats && ["known", "unknown", "marked_known", "marked_unknown", "exams", "freq_cut"].every(k => count(p.stats[k]))
      && Array.isArray(p.lists) && p.lists.every(v => typeof v === "string")
      && p.marks && [p.marks.known, p.marks.unknown].every(a => Array.isArray(a) && a.every(v => typeof v === "string"));
  }

  async function api(path, body) {
    let r;
    try {
      r = await fetch(path, { ...(body ? { method: "POST", headers: H, body: JSON.stringify(body) } : { headers: H }),
        cache: "no-store", signal: AbortSignal.timeout(10000) });
    } catch (error) {
      throw new Error(error.name === "TimeoutError" || error.name === "AbortError"
        ? "请求超时，请核对本机画像后重试" : "连接中断，请确认本地程序正在运行");
    }
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!j || typeof j !== "object" || Array.isArray(j)) throw new Error("本地程序响应不完整，请重新读取画像");
    if (!r.ok || j.error) {
      const error = new Error(j.error || `HTTP ${r.status}`);
      error.details = j;
      if (j.recoverable) showRecovery(error);
      throw error;
    }
    if (["/api/profile", "/api/mark", "/api/profile/import", "/api/profile/clear"].includes(path) && !validProfile(j))
      throw new Error("画像响应不完整，请重新读取核对");
    return j;
  }

  // ------------------------------------------------------------ steps 1–2 ---
  function renderForm() {
    const prof = (P && P.profile) || {};
    identity = prof.identity || null;
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
    if (!profileReady) return;
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
      if (!T || T.stage === "intro" || (T.stage === "result" && T.saveState === "saved")) { T = null; renderTest(); }
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
    if (P) renderMarks();
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
      else if (e.target.id === "resetMarks") {
        if (!confirm("清空全部认识 / 不认识标记？身份、成绩和词汇测试仍会保留。")) return;
        await api("/api/profile", { reset_marks: true });
      }
      else return;
      P = await api("/api/profile");
      renderSummary();
    } catch (err) { alert(err.message); }
  });

  // Portable backups contain the saved learner profile, never service credentials.
  let pendingBackup = null, pendingRev = null;
  const dataMessage = (text, bad = false) => {
    $("#dataMessage").textContent = text;
    $("#dataMessage").className = "msg " + (bad ? "bad" : "ok");
  };
  function cancelRestore() {
    pendingBackup = null; pendingRev = null;
    $("#restorePreview").hidden = true;
    $("#backupFile").value = "";
  }
  async function refreshProfile(value) {
    if (!validProfile(value)) throw new Error("画像响应不完整，请重新读取核对");
    P = value; T = null;
    setProfileReady(true);
    renderForm(); renderSummary(); await renderTest();
  }
  function showRecovery(error) {
    setProfileReady(false);
    $("#profileConnection").hidden = true;
    recoveryFingerprint = error.details.fingerprint;
    $("#recoveryPanel").hidden = false;
    $("#recoveryMessage").textContent = error.message;
    for (const id of ["s1", "s2", "s3", "s4", "summary"]) $("#" + id).hidden = true;
    for (const id of ["exportProfile", "chooseBackup", "clearProfile"]) $("#" + id).disabled = true;
  }
  $("#rebuildProfile").addEventListener("click", async () => {
    if (!recoveryFingerprint || !confirm("先完整保留原始文件，再重建为空画像？重建后可以导入有效备份。")) return;
    const button = $("#rebuildProfile"); button.disabled = true;
    try {
      const result = await api("/api/profile/recover", { fingerprint: recoveryFingerprint });
      if (!validProfile(result.profile) || typeof result.backup_path !== "string") throw new Error("恢复响应不完整，请刷新后核对原始备份及画像");
      recoveryFingerprint = null; $("#recoveryPanel").hidden = true;
      for (const id of ["s1", "s2", "s3"]) $("#" + id).hidden = false;
      for (const id of ["exportProfile", "chooseBackup", "clearProfile"]) $("#" + id).disabled = false;
      await refreshProfile(result.profile);
      dataMessage(`原始文件已保留在 ${result.backup_path}。画像已重建，可选择有效备份恢复。`);
    } catch (error) { $("#recoveryMessage").textContent = error.message; }
    finally { button.disabled = false; }
  });
  $("#exportProfile").addEventListener("click", async () => {
    try {
      const backup = await api("/api/profile/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = `adaptive-subs-profile-${new Date().toISOString().slice(0, 10)}.json`;
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      dataMessage("备份已交给浏览器下载，请检查下载列表。");
    } catch (e) { dataMessage(e.message, true); }
  });
  $("#chooseBackup").addEventListener("click", () => { if (profileReady) $("#backupFile").click(); });
  $("#cancelRestore").addEventListener("click", cancelRestore);
  $("#backupFile").addEventListener("change", async () => {
    const file = $("#backupFile").files[0];
    cancelRestore();
    if (!file || !profileReady) return;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("备份超过 5 MB，请检查是否选对了文件");
      const backup = JSON.parse(await file.text());
      const summary = await api("/api/profile/import/preview", { backup });
      P = await api("/api/profile");
      pendingBackup = backup; pendingRev = P.rev;
      $("#restoreSummary").textContent = `${summary.identity}；认识 ${summary.known} 条，不认识 ${summary.unknown} 条；${summary.test_date ? "测试日期 " + summary.test_date : "没有词汇测试"}。`;
      $("#restorePreview").hidden = false;
      dataMessage("文件校验通过，尚未修改当前画像。");
    } catch (e) { dataMessage(e instanceof SyntaxError ? "文件不是有效的 JSON 备份" : e.message, true); }
  });
  $("#restoreProfile").addEventListener("click", async () => {
    if (!pendingBackup || !profileReady) return;
    const btn = $("#restoreProfile"); btn.disabled = true;
    try {
      const value = await api("/api/profile/import", { backup: pendingBackup, expected_rev: pendingRev });
      cancelRestore(); await refreshProfile(value);
      dataMessage("画像已恢复。播放器和插件会在下次读取画像时更新。");
    } catch (e) { dataMessage(e.message, true); }
    finally { btn.disabled = false; }
  });
  $("#clearProfile").addEventListener("click", async () => {
    if (!profileReady || !P || !confirm("清除本机的身份、成绩、词汇测试和全部认识 / 不认识标记？仅能用你保存的备份恢复。")) return;
    const btn = $("#clearProfile"); btn.disabled = true;
    try {
      const value = await api("/api/profile/clear", { expected_rev: P.rev });
      cancelRestore(); await refreshProfile(value);
      dataMessage("本机画像和标记已清除。");
    } catch (e) { dataMessage(e.message, true); }
    finally { btn.disabled = false; }
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
    const r = await fetch("/data/vocab_test.json", { signal: AbortSignal.timeout(10000) });
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
      const saved = T.saveState === "saved", busy = T.saveState === "saving" || T.saveState === "checking";
      const message = saved ? "本机画像已记录本次结果；播放器和插件下次读取画像后更新。"
        : busy ? (T.saveState === "checking" ? "正在读取本机画像，核对本次结果…" : "测试已完成，正在保存到本机画像…")
        : "本次结果尚未确认保存，作答仍保留在此页面。请先核对或重新保存；关闭页面会丢失未保存的作答。";
      box.innerHTML = `
        <div class="big">≈ ${fmt(r.est)}<small>词</small></div>
        <p class="msg ${saved ? "ok" : busy ? "" : "bad"}" role="status" data-test-save>${esc(message)}</p>
        ${T.saveError ? `<p class="msg bad">${esc(T.saveError)}</p>` : ""}
        <p class="muted" style="margin:4px 0 0">${P && P.prior ? `按考试成绩 / 身份的估计是 ≈ ${fmt(P.prior)}。` : ""}</p>
        <div class="bands">${r.bands.map((x) => `<div class="band" title="${Math.round(x * 100)}%"><div class="band-bar" style="height:${Math.max(2, x * 100)}%"></div></div>`).join("")}</div>
        <div class="band-labels">${bank.bands.map((b) => `<span>${bandLabel(b)}</span>`).join("")}</div>
        <div class="note">每根柱子是一个词频段里你大概认识的比例，越往右越难。<br>
          假词：${r.nonYes ? `${r.nonTotal} 个里有 ${r.nonYes} 个选了「认识」，结果已经往下校正` : `${r.nonTotal} 个都没选「认识」`}。
          ${r.checkTotal ? `词义核对：答对 ${r.checkOk} / ${r.checkTotal}${r.checkOk < r.checkTotal ? "，答错的词频段已下调，答错的词会继续注释" : ""}。` : ""}<br>
          本次作答：认识 ${r.known.length} 个词，不认识 ${r.unknown.length} 个词。${saved ? "这些回答已写入画像。" : "确认保存后才会用于字幕判断。"}</div>
        <div class="actions">
          ${!saved ? `<button class="btn" type="button" data-act="check-save" ${busy ? "disabled" : ""}>核对保存结果</button>
            <button class="btn primary" type="button" data-act="retry-save" ${busy ? "disabled" : ""}>重新保存本次结果</button>` : ""}
          <button class="btn" type="button" data-act="again" ${busy ? "disabled" : ""}>再测一次</button></div>`;
    }
  }

  $("#test").addEventListener("click", async (e) => {
    if (!profileReady) return;
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
    else if (act === "check-save") { checkSavedTest(); }
    else if (act === "retry-save") { finish(); }
    else if (act === "again") {
      if (T.saveState !== "saved" && !confirm("本次结果尚未确认保存，重新测试会丢弃当前页面的作答。继续？")) return;
      T = null; renderTest();
    }
  });

  const testBusy = t => t && (t.saveState === "saving" || t.saveState === "checking");
  const testPayload = r => ({ est: r.est, bands: r.bands, known: r.known, unknown: r.unknown });
  const sameTestSummary = (saved, result) => saved && saved.est === result.est
    && Array.isArray(saved.bands) && saved.bands.length === result.bands.length
    && saved.bands.every((n, i) => Number.isFinite(n) && Math.abs(n - result.bands[i]) <= 0.000501);
  function sameTest(saved, result) {
    const words = xs => Array.isArray(xs) && xs.every(w => typeof w === "string")
      ? JSON.stringify([...new Set(xs.map(w => w.toLowerCase()))].sort()) : null;
    return sameTestSummary(saved, result)
      && words(saved.known) === words(result.known) && words(saved.unknown) === words(result.unknown);
  }
  async function reconcileTest(test) {
    const backup = await api("/api/profile/export");
    if (!sameTest(backup.profile?.test, test.result)) return false;
    if (T !== test) return false;
    test.saveState = "saved";
    test.saveError = "";
    try {
      const current = await api("/api/profile");
      if (T === test) { P = current; renderSummary(); }
    } catch { test.saveError = "本次结果已核对保存，画像概览暂时无法更新，请稍后重新打开页面。"; }
    return true;
  }
  async function checkSavedTest() {
    const test = T;
    if (!test?.result || testBusy(test)) return;
    test.saveState = "checking"; test.saveError = "";
    await renderTest();
    try {
      if (!await reconcileTest(test)) test.saveError = "当前读取的画像与本次作答不同，可以重新保存本次结果。";
    } catch (error) { test.saveError = error.message; }
    if (test.saveState !== "saved") test.saveState = "unconfirmed";
    if (T === test) await renderTest();
  }
  async function finish() {
    const test = T;
    if (!test || testBusy(test)) return;
    test.result ||= score();
    test.stage = "result";
    test.saveState = "saving"; test.saveError = "";
    await renderTest();
    try {
      const current = await api("/api/profile", { test: testPayload(test.result) });
      if (T !== test) return;
      if (!sameTestSummary(current.test, test.result)) throw new Error("保存响应未确认本次测试结果");
      P = current;
      test.saveState = "saved";
      renderSummary();
    } catch (e) {
      test.saveError = e.message;
      // A write may have succeeded before its response was lost. Read, never automatically replay it.
      try { await reconcileTest(test); } catch { /* preserve the current attempt and show it as unconfirmed */ }
      if (test.saveState !== "saved") test.saveState = "unconfirmed";
    }
    if (T !== test) return;
    await renderTest();
    $("#s3").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ boot ---
  async function initializeProfile() {
    if (profileLoading) return;
    profileLoading = true;
    setProfileReady(false);
    $("#profileConnection").hidden = false;
    $("#reloadProfile").disabled = true;
    $("#profileConnectionMessage").className = "msg";
    $("#profileConnectionMessage").textContent = "正在读取本机画像，请稍候…";
    try {
      P = await api("/api/profile");
      setProfileReady(true);
      renderForm(); renderSummary(); await renderTest();
    } catch (e) {
      if (e.details?.recoverable) { showRecovery(e); return; }
      $("#profileConnectionMessage").className = "msg bad";
      $("#profileConnectionMessage").textContent = `暂时无法读取画像：${e.message}。现有记录未改动，请恢复后重新读取；画像备份仍可尝试下载。`;
    } finally {
      profileLoading = false;
      $("#reloadProfile").disabled = false;
    }
  }
  $("#reloadProfile").addEventListener("click", initializeProfile);
  initializeProfile();
})();
