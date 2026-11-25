"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

function fixture() {
  const data = new Map();
  const storage = {
    async get(keys) {
      return Object.fromEntries([].concat(keys).filter(k => data.has(k)).map(k => [k, structuredClone(data.get(k))]));
    },
    async set(changes) { Object.entries(changes).forEach(([k, v]) => data.set(k, structuredClone(v))); },
    async remove(keys) { keys.forEach(k => data.delete(k)); },
  };
  const origin = "chrome-extension://extension/";
  const chrome = { runtime: { id: "extension", getURL: url => origin + url }, storage: { local: storage } };
  let queue = Promise.resolve();
  const locks = { request(name, f) { const result = queue.then(f); queue = result.catch(() => {}); return result; } };
  const context = vm.createContext({ chrome, navigator: { locks }, crypto: { randomUUID } });
  const store = vm.runInContext(fs.readFileSync(path.join(__dirname, "../extension/study-store.js"), "utf8") + "\nAdaptiveStudyStore", context);
  const tab = { id: "extension", frameId: 0, url: "https://www.youtube.com/watch?v=test", tab: { id: 2 }, documentId: "original" };
  const control = { id: "extension", url: origin + "study.html?tab=2", documentId: "settings", tab: { id: 3 }, frameId: 0 };
  const start = () => store.handle({ type: "studyBegin", clip: "c1", domain: "科技", field: "b_initial", fingerprint: "0".repeat(64), subtitleSha256: "b".repeat(64) }, tab);
  return { data, store, tab, control, start };
}
const row = { start: 0, end: 4, word: "bank", shown: false, pUnknown: .35, event: "none" };

test("one consented document owns the draft, export is sealed and deletion is final", async () => {
  const { store, data, tab, control, start } = fixture();
  const results = await Promise.allSettled([start(), start()]);
  assert.equal(results.filter(r => r.status === "fulfilled").length, 1);
  const { id } = results.find(r => r.status === "fulfilled").value;
  const observe = (rows, sender = tab, cue = 1) => store.handle({ type: "studyObserve", id, cue, rows }, sender);
  await assert.rejects(observe([row], { ...tab, documentId: "replacement" }), /发起记录/);
  await assert.rejects(store.handle({ type: "studyExport", id }, control));
  await Promise.all([observe([row]), observe([{ ...row, event: "lookup" }]), observe([{ ...row, event: "unknown" }])]);
  await observe([{ ...row, event: "lookup" }]);
  await observe([{ ...row, shown: true, event: "known" }]);
  // A bad second token rolls back the complete batch.
  await assert.rejects(observe([row, { ...row, pUnknown: NaN }], tab, 2));
  await store.handle({ type: "studyStop", id }, control);
  assert.equal((await observe([row], tab, 3)).stopped, true);
  const result = await store.handle({ type: "studyExport", id }, control);
  assert.equal(result.exposures.length, 1);
  assert.equal(result.exposures[0].event, "unknown");
  assert.equal(result.exposures[0].shown, false);
  await assert.rejects(store.handle({ type: "studyClear", id }, tab));
  await store.handle({ type: "studyClear", id }, control);
  await assert.rejects(observe([row]));
  assert.equal(data.size, 0);
});

test("navigation seals only the owner tab and the capacity limit preserves an exportable prefix", async () => {
  const { store, data, tab, control, start } = fixture();
  const { id } = await start();
  await store.expireTab(19);
  assert.equal((await store.handle({ type: "studyStatus" }, control)).draft.recording, true);
  for (let cue = 0; cue < 51; cue++) {
    const rows = Array.from({ length: 99 }, (_, i) => ({ ...row, start: 5 * i, end: 5 * i + 4 }));
    await store.handle({ type: "studyObserve", id, cue, rows }, tab);
  }
  const result = await store.handle({ type: "studyExport", id }, control);
  assert.equal(result.exposures.length, 5000);
  assert.equal(result.stopReason, "capacity");
  await store.handle({ type: "studyClear", id }, control);
  const fresh = await start();
  await store.expireTab(tab.tab.id);
  assert.equal((await store.handle({ type: "studyExport", id: fresh.id }, control)).stopReason, "pageChanged");
  assert.equal(data.size, 1);
});
