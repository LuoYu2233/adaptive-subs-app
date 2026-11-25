const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { create } = require("../shared/profile-sync.js");
const profile = (rev, state) => ({ rev, level: 4500, known: state === "known" ? ["bank"] : [],
  unknown: state === "unknown" ? ["bank"] : [], marks: { known: state === "known" ? ["bank"] : [], unknown: state === "unknown" ? ["bank"] : [] } });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

test("rapid opposing marks reach the server in order and keep the latest intent visible", async () => {
  let view; const writes = [], a = deferred(), b = deferred();
  const sync = create({ read: async () => profile(0), apply: p => view = p,
    write: op => { writes.push(op.state); return writes.length === 1 ? a.promise : b.promise; } });
  await sync.refresh();
  const first = sync.mutate({ type: "mark", key: "bank", state: "known" });
  const second = sync.mutate({ type: "mark", key: "bank", state: "unknown" });
  await tick();
  assert.deepEqual(writes, ["known"]);
  assert.deepEqual(view.unknown, ["bank"]);
  a.resolve(profile(1, "known")); await first; await tick();
  assert.deepEqual(writes, ["known", "unknown"]);
  assert.deepEqual(view.unknown, ["bank"]);
  b.resolve(profile(2, "unknown")); await second;
  assert.deepEqual(view.known, []); assert.equal(view.rev, 2);
});

test("failed writes roll back while later queued feedback can still succeed", async () => {
  let view, calls = 0; const a = deferred(), b = deferred();
  const sync = create({ read: async () => profile(0), apply: p => view = p,
    write: () => ++calls === 1 ? a.promise : b.promise });
  await sync.refresh();
  const first = sync.mutate({ type: "mark", key: "bank", state: "known" });
  const failed = assert.rejects(first, /disk full/);
  const second = sync.mutate({ type: "mark", key: "bank", state: "unknown" });
  a.reject(new Error("disk full")); await failed; await tick();
  assert.deepEqual(view.unknown, ["bank"]);
  b.resolve(profile(1, "unknown")); await second;
  assert.equal(calls, 2); assert.equal(view.rev, 1);
});

test("an old profile read cannot overwrite a newer saved revision", async () => {
  let view, reads = 0; const old = deferred();
  const sync = create({ read: () => ++reads === 1 ? Promise.resolve(profile(0)) : old.promise,
    write: async () => profile(1, "known"), apply: p => view = p });
  await sync.refresh(); const refresh = sync.refresh();
  await sync.mutate({ type: "mark", key: "bank", state: "known" });
  old.resolve(profile(0)); await refresh;
  assert.equal(view.rev, 1); assert.deepEqual(view.known, ["bank"]);
});

test("a lost response reconciles saved state without replaying the mutation", async () => {
  let view, calls = 0, persisted = profile(0);
  const sync = create({ read: async () => persisted, apply: p => view = p,
    write: async () => { calls++; persisted = profile(1, "unknown"); throw new Error("response lost"); } });
  await sync.refresh();
  await assert.rejects(sync.mutate({ type: "mark", key: "bank", state: "unknown" }), /response lost/);
  assert.equal(calls, 1); assert.deepEqual(view.unknown, ["bank"]); assert.equal(view.rev, 1);
});

test("failed level saves restore the last confirmed value", async () => {
  let view;
  const sync = create({ read: async () => profile(0), apply: p => view = p,
    write: async () => { throw new Error("offline"); } });
  await sync.refresh();
  const change = sync.mutate({ type: "level", level: 9000 });
  assert.equal(view.level, 9000);
  await assert.rejects(change); assert.equal(view.level, 4500);
});

test("clients cannot mark before loading a profile and ship matching sync logic", async () => {
  let calls = 0;
  const sync = create({ read: async () => ({}), write: async () => calls++, apply: () => {} });
  await assert.rejects(sync.mutate({ type: "mark", key: "bank", state: "known" }));
  assert.equal(calls, 0);
  const root = path.join(__dirname, ".."), source = fs.readFileSync(path.join(root, "shared/profile-sync.js"), "utf8");
  for (const relative of ["app/web/assets/profile-sync.js", "extension/profile-sync.js"]) {
    assert.equal(fs.readFileSync(path.join(root, relative), "utf8"), source, "Run python tools/sync_assets.py");
  }
});

test("invalid acknowledgements cannot turn an unchanged profile into a successful mark", async () => {
  const op = { type: "mark", key: "bank", state: "unknown" };
  for (const reply of [null, {}, { ok: true }, { rev: 3 }, profile(0, "unknown"), profile(1, "known"),
    { ...profile(2, "unknown"), level: 0 }, { ...profile(2, "unknown"), known: [null] }]) {
    let posts = 0;
    const sync = create({ read: async () => profile(0), apply: () => {}, write: async () => { posts++; return reply; } });
    await sync.refresh();
    await assert.rejects(sync.mutate(op));
    assert.equal(posts, 1);
  }
});

test("legacy mark receipts require a read at or after the receipt revision with the requested mark", async () => {
  const op = { type: "mark", key: "bank", state: "unknown" };
  for (const [snapshot, succeeds] of [[profile(2, "unknown"), true], [profile(3, "unknown"), true],
    [profile(1, "unknown"), false], [profile(3, "known"), false]]) {
    let reads = 0;
    const sync = create({ read: async () => ++reads === 1 ? profile(0) : snapshot,
      write: async () => ({ ok: true, rev: 2 }), apply: () => {} });
    await sync.refresh();
    if (succeeds) await sync.mutate(op);
    else await assert.rejects(sync.mutate(op));
  }
});

test("a newer refresh that supersedes an in-flight mark remains visible", async () => {
  const delayed = deferred(); let reads = 0, view;
  const sync = create({ read: async () => ++reads === 1 ? profile(0) : profile(2, "known"),
    write: () => delayed.promise, apply: p => view = p });
  await sync.refresh();
  const mark = sync.mutate({ type: "mark", key: "bank", state: "unknown" });
  await tick(); await sync.refresh();
  delayed.resolve(profile(1, "unknown"));
  await assert.rejects(mark);
  assert.deepEqual(view.marks.known, ["bank"]); assert.equal(view.rev, 2);
});

test("level replies must confirm the requested value", async () => {
  let view;
  const sync = create({ read: async () => profile(0), write: async () => profile(1), apply: p => view = p });
  await sync.refresh();
  await assert.rejects(sync.mutate({ type: "level", level: 9000 }));
  assert.equal(view.level, 4500);
});
