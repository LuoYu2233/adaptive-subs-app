const { test } = require("node:test");
const assert = require("node:assert/strict");
const W = require("../app/web/assets/wordbook.js");
const entry = { key: "ctx:bank:finance", mk: "ctx:bank:finance", term: "bank", zh: "银行", kind: "sense",
  lv: 5000, sentence: "😀 The bank approved my loan.", t: 1.25, vid: "abcdefghijk", added: 1700000000000, hasNote: true };

test("wordbook backup round trips meanings, Unicode, fractional time and legacy entries", () => {
  const legacy = { ...entry, key: "bank" }; delete legacy.mk; delete legacy.hasNote;
  const original = [entry, legacy];
  assert.deepEqual(W.parse(JSON.parse(JSON.stringify(W.backup(original)))), original);
  assert.deepEqual(W.parse(W.backup([])), []);
});
test("merge preserves existing details and distinguishes two meanings without modifying inputs", () => {
  const incoming = [{ ...entry, zh: "do not overwrite" }, { ...entry, key: "ctx:bank:shore", zh: "河岸" }];
  const original = structuredClone(incoming), current = [structuredClone(entry)];
  const merged = W.merge(current, incoming);
  assert.equal(merged.length, 2); assert.equal(merged[0].zh, "银行"); assert.equal(merged[1].zh, "河岸");
  assert.deepEqual(incoming, original); assert.deepEqual(current, [entry]);
  assert.deepEqual(W.merge(merged, incoming), merged);
});
test("imports reject incompatible versions and any invalid entry atomically", () => {
  for (const value of [null, [], {}, { format: "adaptive-subs-profile", version: 1, entries: [] },
    { format: "adaptive-subs-wordbook", version: 2, entries: [] }]) assert.throws(() => W.parse(value));
  const invalid = [null, [], { ...entry, key: "" }, { ...entry, sentence: "x".repeat(10001) },
    { ...entry, term: "\ud800" }, { ...entry, t: -1 }, { ...entry, t: Infinity }, { ...entry, lv: "5000" },
    { ...entry, vid: "../file" }, { ...entry, kind: '<img src=x>' }, { ...entry, added: 1.5 },
    { ...entry, hasNote: "false" }, { ...entry, mk: "" }];
  for (const item of invalid) assert.throws(() => W.entries([entry, item]));
  assert.throws(() => W.entries([entry, { ...entry }]));
  assert.throws(() => W.entries(Array(W.MAX_ENTRIES + 1).fill(entry)));
});
test("unrecognized fields are stripped and merged entry limit is enforced", () => {
  const extra = JSON.parse(JSON.stringify(entry).replace(/}$/, ',"__proto__":{"polluted":true},"extra":"unused"}'));
  assert.deepEqual(W.entries([extra]), [entry]);
  assert.equal({}.polluted, undefined);
  const full = Array.from({ length: W.MAX_ENTRIES }, (_, i) => ({ ...entry, key: `word:${i}` }));
  assert.throws(() => W.merge(full, [entry]));
  assert.equal(W.merge(full, [full[0]]).length, W.MAX_ENTRIES);
});
test("CSV cells escape quotes, line breaks and spreadsheet formula prefixes", () => {
  assert.equal(W.csvCell('a,"b"'), '"a,""b"""');
  assert.equal(W.csvCell("😀 银行"), '"😀 银行"');
  for (const value of ["=1+1", "+SUM(A1)", "-2+3", "@SUM(A1)", "  =1", "\uFEFF=1", "\ttext", "\rtext", "\ntext"])
    assert.equal(W.csvCell(value), `"'${value}"`);
  assert.equal(W.csvCell("line\nbreak"), '"line\nbreak"');
});
