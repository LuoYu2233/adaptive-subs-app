const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { markKey, classify, selectNotes } = require("../app/web/assets/prediction.js");

const word = (key, lv, kind = "word") => ({ key, lv, kind });
const bank = word("bank", 4000);
const shore = word("bank", 4000, "sense");
const phrase = word("take off", 3500, "phrase");
const preference = { perLine: 2, repeat: 1 };
const none = new Set();

test("saved keys retain their meaning and explicit unknown wins conflicts", () => {
  assert.equal(markKey(bank), "bank");
  assert.equal(markKey(shore), "sense:bank");
  assert.equal(markKey(phrase), "phrase:take off");
  const known = new Set(["bank", "phrase:take off"]);
  assert.equal(classify(bank, known, new Set(["bank"]), 9000), "forced");
  assert.equal(classify(bank, known, none, 3000), "known");
  assert.equal(classify(shore, known, none, 3000), "hard");
  assert.equal(classify(phrase, known, none, 3000), "known");
});

test("the existing difficulty cutoff is strict, including at the preference-adjusted boundary", () => {
  assert.equal(classify(bank, none, none, 5000 * 0.8), "easy");
  assert.equal(classify(bank, none, none, 3999), "hard");
});

test("prioritize explicit unknown, then difficulty, retaining input order for ties", () => {
  const notes = [word("early", 6000), word("later", 6000), word("forced", 500)];
  const state = n => classify(n, none, new Set(["forced"]), 4000);
  const counts = new Map();
  assert.deepEqual([...selectNotes(notes, state, preference, counts)], [2, 0]);
  assert.deepEqual([...selectNotes(notes, state, preference, counts)], [2, 1]);
  assert.deepEqual([...selectNotes(notes, state, preference, counts)], [2]);
  assert.deepEqual([...selectNotes(notes, state, preference, new Map())], [2, 0]);
});

test("explicit unknown overrides repeat limits but still respects the per-line cap", () => {
  const notes = [bank, shore, phrase];
  const unknown = new Set(notes.map(markKey));
  const state = n => classify(n, none, unknown, 10000);
  const counts = new Map();
  assert.deepEqual([...selectNotes(notes, state, preference, counts)], [0, 1]);
  assert.deepEqual([...selectNotes(notes, state, preference, counts)], [0, 1]);
  assert.equal(counts.has(markKey(phrase)), false);
});

test("both shipped clients load the same predictor before their consuming script", () => {
  const read = name => readFileSync(path.join(__dirname, "..", name), "utf8");
  const source = read("app/web/assets/prediction.js");
  assert.equal(read("extension/prediction.js"), source);
  const context = vm.createContext({});
  vm.runInContext(source, context);
  assert.equal(context.AdaptivePrediction.classify(bank, none, none, 3000), "hard");
  const scripts = JSON.parse(read("extension/manifest.json")).content_scripts
    .filter(entry => entry.js?.includes("content.js"));
  assert.ok(scripts.length > 0);
  for (const { js } of scripts) assert.ok(js.includes("prediction.js") && js.indexOf("prediction.js") < js.indexOf("content.js"));
  assert.match(read("app/web/player.html"), /src="assets\/prediction\.js"[\s\S]+src="assets\/app\.js"/);
});

test("persisted code-point positions match browser slices, including repeated preparation and local marks", () => {
  const { clientNotes } = require("../app/web/assets/prediction.js");
  const text = "😀𝄞 e\u0301 bank and 🪙.";
  const source = ["e\u0301","bank","🪙"].map(term => {
    const at = text.indexOf(term), s = [...text.slice(0,at)].length;
    return { s, e:s + [...term].length, key:term };
  });
  const local = {s:text.indexOf("and"),e:text.indexOf("and")+3,local:true,offsets:"utf16"};
  const ready = clientNotes(text,[...source,local,{s:0,e:500}]);
  assert.deepEqual(ready.map(({s,e})=>text.slice(s,e)),["e\u0301","bank","🪙","and"]);
  assert.deepEqual(clientNotes(text, ready),ready);
  assert.deepEqual(source.map(row=>row.offsets),[undefined,undefined,undefined]);
  assert.equal(clientNotes("ASCII",[{s:0,e:5}])[0].e,5);
});
