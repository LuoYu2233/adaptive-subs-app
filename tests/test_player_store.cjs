const { test } = require("node:test");
const assert = require("node:assert/strict");
const P = require("../app/web/assets/player-store.js");
function fixture(raw = null) {
  const data = new Map(raw === null ? [] : [[P.KEY, raw]]), queues = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)),
    get length() { return data.size; }, key: index => [...data.keys()][index] ?? null };
  const manager = { request: (key, { signal }, action) => {
    const result = (queues.get(key) || Promise.resolve()).then(() => { signal.throwIfAborted(); return action(); });
    queues.set(key, result.catch(() => {})); return result;
  } };
  return { data, storage, create: () => P.create({ storage: () => storage, locks: () => manager }) };
}
test("invalid saved JSON and field types fail without turning into an empty record", async () => {
  for (const raw of ['', '{broken', 'null', '[]', '{"known":{}}', '{"book":[null]}', '{"mode":"other"}',
    '{"storageVersion":2}', '{"test":{"est":"wrong","date":"2026-11-24"}}']) {
    const f = fixture(raw), store = f.create();
    assert.throws(() => store.read(), error => error.code === "damaged" && error.raw === raw);
    await assert.rejects(store.update(current => ({ ...current, mode: "plain" })), { code: "damaged" });
    assert.equal(f.data.get(P.KEY), raw);
  }
});
test("cooperating clients apply edits to the latest state and preserve unrelated fields", async () => {
  const f = fixture('{"book":[],"futureInfo":{"keep":true}}'), a = f.create(), b = f.create();
  await Promise.all(Array.from({ length: 60 }, (_, i) => (i % 2 ? a : b).update(current => ({
    ...current, count: (current.count || 0) + 1, ...(i % 2 ? { mode: "plain" } : { gloss: "paren" }),
  }))));
  const value = a.read();
  assert.equal(value.count, 60); assert.equal(value.mode, "plain"); assert.equal(value.gloss, "paren");
  assert.deepEqual(value.futureInfo, { keep: true }); assert.deepEqual(value.book, []);
});
test("a failed write and an invalid edit do not poison later queued saves", async () => {
  const f = fixture('{"mode":"adaptive"}'), store = f.create(), put = f.storage.setItem;
  f.storage.setItem = () => { throw new Error("quota"); };
  await assert.rejects(store.update(v => ({ ...v, mode: "plain" })), /quota/);
  assert.equal(store.read().mode, "adaptive"); f.storage.setItem = put;
  await assert.rejects(store.update(v => ({ ...v, book: [null] })));
  await store.update(v => ({ ...v, mode: "plain" })); assert.equal(store.read().mode, "plain");
});
test("recovery preserves exact raw data before replacing and refuses healthy or changed data", async () => {
  const raw = '{"book":["original",', f = fixture(raw), store = f.create();
  await assert.rejects(store.recover("stale"), { code: "conflict" });
  const key = await store.recover(raw);
  assert.equal(f.data.get(key), raw); assert.equal(store.recoveryCopies()[0].raw, raw);
  assert.equal(store.read().storageVersion, 1);
  await assert.rejects(store.recover(f.data.get(P.KEY)), /无需重建/);
  assert.equal(store.recoveryCopies().length, 1);
});
test("backup or replacement failure leaves the corrupt original untouched", async () => {
  for (const failure of ["backup", "replace"]) {
    const raw = "corrupt", f = fixture(raw), store = f.create(), put = f.storage.setItem;
    f.storage.setItem = (key, value) => {
      if ((failure === "replace") === (key === P.KEY)) throw new Error(failure);
      put(key, value);
    };
    await assert.rejects(store.recover(raw), new RegExp(failure));
    assert.equal(f.data.get(P.KEY), raw);
    assert.equal(store.recoveryCopies().length, failure === "replace" ? 1 : 0);
  }
});
test("unavailable storage or locks never silently disables persistence protection", async () => {
  const noStorage = P.create({ storage: () => { throw new Error("blocked"); } });
  assert.throws(() => noStorage.read(), { code: "unavailable" });
  const noLocks = P.create({ storage: () => fixture().storage, locks: () => undefined });
  await assert.rejects(noLocks.update(() => ({})), { code: "unsupported" });
  const timed = P.create({ storage: () => fixture().storage, timeout: 5,
    locks: () => ({ request: (_, { signal }) => new Promise((resolve, reject) =>
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))) }) });
  await assert.rejects(timed.update(() => ({})), { code: "busy" });
});
