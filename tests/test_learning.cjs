const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const L = require("../shared/learning.js");
const bank = { key: "bank", kind: "word", zh: "银行", lv: 5000 };
const shore = { key: "bank", kind: "sense", zh: "河岸", lv: 5000 };

test("learning one meaning does not hide another meaning of the same word", () => {
  const known = new Set([L.key(bank)]);
  assert.equal(L.state(bank, known, new Set(), 3000), "known");
  assert.equal(L.state(shore, known, new Set(), 3000), "hard");
  assert.notEqual(L.key(bank), L.key(shore));
});
test("exact feedback overrides word-wide estimates and old marks", () => {
  assert.equal(L.state(bank, new Set(["bank"]), new Set([L.key(bank)]), 10000), "forced");
  assert.equal(L.state(bank, new Set([L.key(bank)]), new Set(["bank"]), 3000), "known");
  assert.equal(L.state(shore, new Set(["bank"]), new Set(), 3000), "hard");
});
test("old marks still work; spacing and punctuation do not split a meaning", () => {
  assert.equal(L.state(shore, new Set(["sense:bank"]), new Set(), 3000), "known");
  assert.equal(L.key({ ...bank, zh: " 银行。 " }), L.key(bank));
  assert.equal(L.label(L.key(bank)), "bank");
  assert.equal(L.label("phrase:take off"), "take off");
});
test("changed gloss invalidates identity; dictionary words retain old keys", () => {
  const n = { ...bank }, before = L.key(n);
  n.zh = "河岸";
  assert.notEqual(L.key(n), before);
  assert.equal(L.key({ ...bank, local: true }), "bank");
});
test("explicit unfamiliar meanings stay visible beyond density and repetition limits", () => {
  const unknown = new Set([L.key(bank), L.key(shore)]), counts = new Map();
  const preference = { perLine: 1, repeat: 1 };
  for (let i = 0; i < 3; i++) {
    assert.deepEqual([...L.select([bank, shore], new Set(), unknown, 15000, preference, counts)], [0, 1]);
  }
});
test("repetition limits distinguish meanings and still limit ordinary hints", () => {
  const counts = new Map(), pref = { perLine: 1, repeat: 1 };
  const select = notes => [...L.select(notes, new Set(), new Set(), 3000, pref, counts)];
  assert.deepEqual(select([bank]), [0]);
  assert.deepEqual(select([bank]), []);
  assert.deepEqual(select([shore]), [0]);
});
test("both clients ship the canonical learning rules", () => {
  const root = path.join(__dirname, "..");
  const source = fs.readFileSync(path.join(root, "shared/learning.js"), "utf8");
  for (const relative of ["app/web/assets/learning.js", "extension/learning.js"]) {
    assert.equal(fs.readFileSync(path.join(root, relative), "utf8"), source, "Run python tools/sync_assets.py");
  }
});
