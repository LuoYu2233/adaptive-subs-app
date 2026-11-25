const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {validate} = require('../app/web/assets/vocab-bank.js');
const original = JSON.parse(fs.readFileSync(path.join(__dirname,'../app/data/vocab_test.json')));
const copy = () => structuredClone(original);

test('the shipped bank contains enough unique questions and distinct answer choices', () => {
  const before = JSON.stringify(original);
  const bank = validate(original);
  assert.equal(bank.bands.length,9);
  assert.equal(bank.bands.reduce((n,b) => n + b.words.length,0),271);
  assert.equal(bank.nonwords.length,40);
  assert.equal(JSON.stringify(original),before);
  bank.bands[0].words[0].zh = 'changed copy';
  assert.equal(JSON.stringify(original),before);
});

test('empty, incomplete and invalid band ranges cannot start a vocabulary test', () => {
  for (const bad of [null,{},[],{bands:[],nonwords:[]},{...copy(),nonwords:[]}]) assert.throws(() => validate(bad),/题库/);
  for (const mutate of [b => b.bands[0].lo = 0, b => b.bands[1].lo--, b => b.bands[1].hi = NaN,
      b => b.bands[0].hi = true, b => b.bands[0].words = b.bands[0].words.slice(0,10),
      b => b.bands[0].words = Array(201).fill(b.bands[0].words[0]), b => b.nonwords = b.nonwords.slice(0,5)]) {
    const bad=copy();mutate(bad);assert.throws(() => validate(bad),/题库/);
  }
});

test('duplicate real words, fake words and collisions across their lists are rejected', () => {
  for (const mutate of [b => b.bands[1].words[0].word = b.bands[0].words[0].word,
      b => b.nonwords[1] = b.nonwords[0], b => b.nonwords[0] = b.bands[0].words[0].word]) {
    const bad=copy();mutate(bad);assert.throws(() => validate(bad),/题库/);
  }
});

test('duplicate choices and the reserved unsure button cannot appear as answer options', () => {
  for (const mutate of [w => w.distractors[0] = w.zh, w => w.distractors[1] = ' ' + w.distractors[0] + ' ',
      w => w.distractors[0] = '不确定', w => w.zh = '不确定', w => w.distractors = [],
      w => w.example = ' ', w => w.zh = '\ud800', w => w.example = null,
      w => w.word = '<img>', w => w.word = 'x'.repeat(41)]) {
    const bad=copy();mutate(bad.bands[0].words[0]);assert.throws(() => validate(bad),/题库/);
  }
});

test('streaming bank load enforces the byte limit without Content-Length and retries a failed read', async t => {
  const {loader} = require('../app/web/assets/vocab-bank.js');
  let reads = 0, canceled = false;
  t.mock.method(globalThis, 'fetch', async () => {
    reads++;
    if (reads === 1) return new Response(new ReadableStream({
      pull(controller) { controller.enqueue(new Uint8Array(16384)); },
      cancel() { canceled = true; },
    }));
    if (reads === 2) return new Response(new Uint8Array([0xff, 0xfe]));
    return new Response(JSON.stringify(original));
  });
  const load = loader('data/vocab_test.json');
  const first = load();
  assert.equal(first, load());
  await assert.rejects(first, /题库文件过大/);
  assert.equal(canceled, true); assert.equal(reads, 1);
  await assert.rejects(load());
  const bank = await load();
  assert.equal(bank.bands.length, 9);
  assert.equal(await load(), bank);
  assert.equal(reads, 3);
});
