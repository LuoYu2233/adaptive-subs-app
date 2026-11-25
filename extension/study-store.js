/* A single explicitly started viewing study, stored under the extension origin. */
"use strict";
const AdaptiveStudyStore = (() => {
  const META = "wordStudyDraft", limit = 5000;
  const rowKey = (id, cue) => `wordStudy:${id}:${cue}`;
  const integer = x => Number.isSafeInteger(x) && x >= 0;
  const fieldOK = x => ["b_frequency", "b_initial"].includes(x);
  const codeOK = s => typeof s === "string" && /^[\p{L}\p{N}_-]{1,32}$/u.test(s);
  const fail = message => { throw new Error(message); };
  const summary = m => m ? { id: m.id, recording: m.recording, count: m.count, clip: m.clip, domain: m.domain,
    started: m.started, ended: m.ended, reason: m.reason } : null;

  async function handle(msg, sender) {
    if (sender.id !== chrome.runtime.id) fail("来源无效");
    const control = sender.url?.split("?")[0] === chrome.runtime.getURL("study.html");
    const tab = Number.isInteger(sender.tab?.id) && sender.documentId && sender.frameId === 0;
    return navigator.locks.request(META, async () => {
      const storage = chrome.storage.local;
      const meta = (await storage.get(META))[META];
      if (msg.type === "studyBegin") {
        if (!tab || control || !codeOK(msg.clip) || !codeOK(msg.domain) || !fieldOK(msg.field) ||
            ![msg.fingerprint, msg.subtitleSha256, msg.mediaSha256].every(h => typeof h === "string" && /^[a-f0-9]{64}$/.test(h))) fail("片段代号、领域或模型信息无效");
        const p = msg.policy;
        let policy;
        if (p !== undefined) {
          if (!["CAT", "feedback"].includes(p?.source) || ![p.anchorTheta, p.discrimination, p.effectiveTheta].every(Number.isFinite) ||
              p.discrimination <= 0) fail("当前生词模型的测量参数无效");
          policy = { source: p.source, anchorTheta: p.anchorTheta, discrimination: p.discrimination, effectiveTheta: p.effectiveTheta };
          if (p.source === "feedback") {
            if (!integer(p.trainedThrough) || p.trainedThrough > Date.now() || (p.domain !== "" && p.domain !== msg.domain))
              fail("请使用匹配当前视频领域且在观看前生成的校准报告");
            Object.assign(policy, { domain: p.domain, trainedThrough: p.trainedThrough });
          }
        }
        if (meta) fail("本机已有一组记录，请先导出并清除");
        const next = { version: 1, id: crypto.randomUUID(), clip: msg.clip, domain: msg.domain, field: msg.field,
          fingerprint: msg.fingerprint, subtitleSha256: msg.subtitleSha256, mediaSha256: msg.mediaSha256, ...(policy ? { policy } : {}), started: Date.now(), recording: true, count: 0, cues: [],
          tab: sender.tab.id, document: sender.documentId };
        await storage.set({ [META]: next });
        return summary(next);
      }
      if (msg.type === "studyStatus" && control) return { draft: summary(meta) };
      if (!meta || meta.id !== msg.id) fail("记录已结束或已被清除");
      const owner = tab && meta.tab === sender.tab.id && meta.document === sender.documentId;
      if (msg.type === "studyStop" && (control || owner)) {
        if (meta.recording) {
          meta.recording = false; meta.ended = Date.now();
          meta.reason = owner ? "pageChanged" : "stopped";
          await storage.set({ [META]: meta });
        }
        return summary(meta);
      }
      const keys = () => meta.cues.map(c => rowKey(meta.id, c));
      if (msg.type === "studyClear" && control && !meta.recording) {
        await storage.remove([META, ...keys()]);
        return { ok: true };
      }
      if (msg.type === "studyExport" && control && !meta.recording) {
        const saved = await storage.get(keys());
        const exposures = meta.cues.flatMap(c => {
          const records = saved[rowKey(meta.id, c)];
          if (!Array.isArray(records)) fail("本机记录不完整");
          return records.map(r => ({ cue: c, ...r }));
        });
        if (exposures.length !== meta.count) fail("本机记录数量不一致");
        return { version: 1, kind: "word-viewing-study", id: meta.id, clip: meta.clip, domain: meta.domain,
          tableSha256: meta.fingerprint, subtitleSha256: meta.subtitleSha256, mediaSha256: meta.mediaSha256, field: meta.field, ...(meta.policy ? { policy: meta.policy } : {}), started: meta.started, ended: meta.ended,
          stopReason: meta.reason, exposures };
      }
      if (msg.type !== "studyObserve" || !owner) fail("只有发起记录的视频可以填写观看事件");
      if (!meta.recording) return { stopped: true };
      if (!integer(msg.cue) || !Array.isArray(msg.rows)) fail("字幕位置无效");
      const key = rowKey(meta.id, msg.cue);
      const rows = (await storage.get(key))[key] || [];
      let dirty = false;
      for (const r of msg.rows) {
        if (!integer(r?.start) || !integer(r.end) || r.end <= r.start || typeof r.word !== "string" || !r.word.length ||
            typeof r.shown !== "boolean" || !Number.isFinite(r.pUnknown) || r.pUnknown < 0 || r.pUnknown > 1 ||
            !["none", "lookup", "known", "unknown"].includes(r.event)) fail("单词反馈记录无效");
        let prev = rows.find(p => p.start === r.start && p.end === r.end);
        if (!prev && meta.count < limit) {
          prev = { start: r.start, end: r.end, word: r.word, shown: r.shown, pUnknown: r.pUnknown,
            at: Date.now(), event: "none" };
          rows.push(prev); meta.count++; dirty = true;
        }
        if (prev && prev.word === r.word && prev.shown === r.shown && prev.pUnknown === r.pUnknown &&
            r.event !== "none" && (prev.event === "none" || prev.event === "lookup" && r.event !== "lookup")) {
          prev.event = r.event; dirty = true;
        }
      }
      if (dirty) {
        if (!meta.cues.includes(msg.cue)) meta.cues.push(msg.cue);
        if (meta.count >= limit) {
          meta.recording = false; meta.ended = Date.now(); meta.reason = "capacity";
        }
        await storage.set({ [META]: meta, [key]: rows });
      }
      return { stopped: !meta.recording };
    });
  }
  async function expireTab(tabId) {
    await navigator.locks.request(META, async () => {
      const m = (await chrome.storage.local.get(META))[META];
      if (!m?.recording || (tabId !== null && m.tab !== tabId)) return;
      await chrome.storage.local.set({ [META]: { ...m, recording: false, ended: Date.now(), reason: "pageChanged" } });
    });
  }
  return { handle, expireTab };
})();
