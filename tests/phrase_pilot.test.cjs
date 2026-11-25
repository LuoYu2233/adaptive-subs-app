"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { assemble, makePage } = require("../tools/build_phrase_pilot.cjs");
const { Session, scale } = require("../tools/phrases/model.js");
const text = readFileSync(path.join(__dirname, "../tools/phrases/items.tsv"), "utf8");
const bank = assemble(text);
test("100 distinct contextual items, balanced provisional levels and portable content fingerprint", () => {
  assert.equal(bank.items.length, 100);
  for (let level = 1; level <= 5; level++) assert.equal(bank.items.filter(i => i.level === level).length, 20);
  assert.equal(assemble("\uFEFF" + text.replace(/\r?\n/g, "\r\n")).bankSha256, bank.bankSha256);
  assert.throws(() => assemble(text.replace("[look for]", "look for")), /第 2 行/);
  assert.throws(() => assemble(text.replace("\t寻找\t交还", "\t寻找\t寻找")), /选项/);
  const evil = structuredClone(bank); evil.items[0].before = "</script>";
  assert.equal((makePage(evil).match(/<\/script>/g) || []).length, 4);
});

test("tied information draws across the whole level instead of always selecting the first few rows", () => {
  const words = new Set();
  for (let i = 0; i < 100; i++) words.add(new Session(bank, () => (i + .5) / 100).next().item.phrase);
  assert.equal(words.size, 20);
});

test("independent phrase posterior moves with answers and exports only its own scale", () => {
  function complete(answer) {
    const s = new Session(bank, () => .31);
    assert.throws(() => s.result(), /完成/);
    assert.throws(() => s.answer(0), /选择/);
    while (s.next()) {
      assert.equal(s.next(), s.current);
      assert.throws(() => s.answer(4));
      s.answer(answer(s.current));
    }
    return s.result();
  }
  const high = complete(q => q.choices.indexOf(q.item.meaning));
  const low = complete(() => null);
  for (const r of [high, low]) {
    assert.ok(r.responses.length >= 6 && r.responses.length <= 8);
    assert.ok(r.se > 0 && r.se < r.priorSD);
    assert.equal(new Set(r.responses.map(a => a.phrase)).size, r.responses.length);
    assert.equal(r.bankSha256, bank.bankSha256);
    assert.equal(r.scale, scale);
    assert.equal(r.thetaByField, undefined);
  }
  assert.ok(high.thetaPhrase > 0 && low.thetaPhrase < 0);
  assert.ok(low.responses.every(r => !r.correct && r.selected === null));
});
