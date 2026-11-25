const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const L = require("../shared/learning.js");
const bank = { key: "bank", kind: "word", zh: "银行", lv: 5000 };
test("both clients ship the canonical learning rules", () => {
  const root = path.join(__dirname, "..");
  const source = fs.readFileSync(path.join(root, "shared/learning.js"), "utf8");
  for (const relative of ["app/web/assets/learning.js", "extension/learning.js"]) {
    assert.equal(fs.readFileSync(path.join(root, relative), "utf8"), source, "Run python tools/sync_assets.py");
  }
});

test("Python note offsets correctly select words after emoji and supplementary characters", () => {
  for (const prefix of ["😀 The ", "👨‍👩‍👧‍👦 Our ", "𐐀 文字 ", "The "]) {
    const text = prefix + "bank approved it.", s = [...prefix].length;
    const raw = { ...bank, s, e: s + 4 };
    const [note] = L.clientNotes(text, [raw]);
    assert.equal(text.slice(note.s, note.e), "bank");
    assert.equal(raw.s, s); assert.equal(raw.offsets, undefined);
    assert.deepEqual(L.clientNotes(text, [note]), [note], "reused client notes must not be converted twice");
  }
});

test("invalid subtitle positions fail explicitly", () => {
  assert.throws(() => L.clientNotes("bank", [{ ...bank, s: -1, e: 3 }]));
  assert.throws(() => L.clientNotes("bank", [{ ...bank, s: 0, e: 8 }]));
});
