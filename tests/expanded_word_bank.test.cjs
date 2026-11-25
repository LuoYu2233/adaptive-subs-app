"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const bank = require("../app/data/vocab_test.json");
const { readSets, expand } = require("../tools/cat/bank/expand.cjs");
const { assemble } = require("../tools/build_cat_pilot.cjs");
const { Session } = require("../tools/cat/model.js");
const source = fs.readFileSync(path.join(__dirname, "../tools/cat/bank/contrast-sets.tsv"), "utf8");
const originals = bank.bands.flatMap(b => b.words).map(w => w.word);
const all = [...new Set([...originals, ...readSets(source).map(q => q.word)])];
const words = Object.fromEntries(all.map((word, i) => [word, {rank: 300 + 15 * i, b_initial: Math.log(300 + i * 15), b_frequency: Math.log(300 + i * 15)}]));
const table = {version: 1, scale: "natural-log lemma rank", words};
const bytes = Buffer.from(JSON.stringify(table));

test("retain every original item and fill gaps deterministically using distinct within-topic meanings", () => {
  const expanded = expand(table, bank, source), data = assemble(bytes, expanded), old = assemble(bytes,bank);
  assert.equal(data.items.length,800);
  assert.equal(new Set(data.items.map(r => r.word)).size,800);
  assert.deepEqual(data.items.slice(0,old.items.length),old.items);
  assert.equal(data.tableSha256,old.tableSha256);
  assert.notEqual(data.itemBank.sha256,old.itemBank.sha256);
  assert.deepEqual(expand(table,bank,"\uFEFF"+source.replace(/\r?\n/g,"\r\n")),expanded);
  for (const q of data.items.slice(old.items.length)) {
    assert.equal(q.distractors.length,3);
    assert.equal(new Set([q.zh,...q.distractors]).size,4);
    assert.ok(q.contrastSet.split("/").includes(q.word) && q.context);
  }
  const [head, first] = source.trimEnd().split("\n");
  assert.throws(() => readSets([head,first,first].join("\n")), /重复词头/);
  assert.throws(() => expand({ ...table, words: {} },bank,source), /不足 800/);
});

test("a trial cannot reveal the same set of four meanings twice and keeps the bank identity out of live predictions", () => {
  const data = assemble(bytes,expand(table,bank,source)), session = new Session(data,500,()=>.42);
  const seen = new Set();
  while (session.next()) {
    const set = session.current.item.contrastSet;
    if (set) { assert.ok(!seen.has(set)); seen.add(set); }
    session.answer(null);
  }
  assert.ok(seen.size > 0);
  const record = session.result();
  assert.deepEqual(record.itemBank,data.itemBank);
  assert.equal(record.parameters.itemBank,undefined);
});
