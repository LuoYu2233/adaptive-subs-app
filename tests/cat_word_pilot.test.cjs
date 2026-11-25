const test = require("node:test");
const assert = require("node:assert/strict");
const { assemble, makePage } = require("../tools/build_cat_pilot.cjs");
const { Session, information, correctProbability, priorMean, expectedVocabulary } = require("../tools/cat/model.js");
const bank = require("../app/data/vocab_test.json");

function fixture() {
  const rows = bank.bands.flatMap(b => b.words);
  const words = Object.fromEntries(rows.map(({word}, i) => [word, { b_initial: Math.log(i+2), b_frequency: Math.log(i+1) }]));
  return { words, bytes: Buffer.from(JSON.stringify({ version:1, scale:"natural-log lemma rank", words })) };
}
const data = assemble(fixture().bytes, bank);

function complete(respond) {
  const run = new Session(data, 100, () => 0.35);
  while (run.next()) {
    assert.equal(run.next(), run.current);
    assert.ok(run.history.length < 40);
    run.answer(respond(run.current));
  }
  return run.result();
}

test("3PL information matches a numerical derivative and falls to zero at each extreme", () => {
  const b = 4, step = 1e-5;
  for (const t of [1,3,4,4.5,8]) {
    const p = correctProbability(t,b), derivative = (correctProbability(t+step,b)-correctProbability(t-step,b))/(2*step);
    assert.ok(Math.abs(information(t,b) - derivative**2/(p*(1-p))) < 1e-8);
  }
  assert.equal(information(1000,b),0);
  assert.equal(information(-1000,b),0);
  assert.ok(Math.abs(expectedVocabulary(data.difficulties.b_initial,priorMean(data.difficulties.b_initial,100)) - 100)<1e-8);
});

test("answer histories recover direction, retain both calibrated fields and never repeat a question", () => {
  const correct = q => q.choices.indexOf(q.item.zh);
  const high = complete(correct), low = complete(() => null);
  for (const field of ["b_frequency","b_initial"]) {
    assert.ok(high.fits[field].theta > low.fits[field].theta);
    assert.ok(high.fits[field].expectedVocabulary > low.fits[field].expectedVocabulary);
    for (const r of [high,low]) {
      assert.ok(r.fits[field].se > 0 && r.fits[field].se < 0.8);
      assert.equal(r.parameters.thetaByField[field], r.fits[field].theta);
    }
  }
  assert.equal(high.parameters.tableSha256, data.tableSha256);
  for (const r of [high,low]) {
    assert.ok(r.responses.length >= 12 && r.responses.length <= 40);
    assert.equal(new Set(r.responses.map(a=>a.word)).size, r.responses.length);
    assert.ok(r.stopReason === "precision" ? r.fits.b_initial.se < 0.16 : r.responses.length === 40);
  }
  assert.ok(low.responses.every(r => r.selected === null && !r.correct));
});

test("conditional next question follows answers, refuses invalid transitions and enforces distinct choices", () => {
  const run = new Session(data, 100, () => 0);
  assert.throws(()=>run.answer(0)); assert.throws(()=>run.result());
  const q = run.next();
  assert.equal(new Set(q.choices).size,4); assert.throws(()=>run.answer(4)); assert.throws(()=>run.answer("1"));
  run.answer(q.choices.indexOf(q.item.zh));
  const other = new Session(data,100,()=>0);
  assert.equal(other.next().item.word,q.item.word); other.answer(null);
  assert.notEqual(other.next().item.word,run.next().item.word);
});

test("builder reads real four-choice items, validates scales, and safely embeds literal text", () => {
  assert.equal(data.items.length,271);
  assert.throws(()=>assemble(Buffer.from("{}"),bank));
  const broken = structuredClone(bank); broken.bands[0].words[0].distractors.fill("重复");
  assert.throws(()=>assemble(fixture().bytes,broken),/四选一/);
  const sample = structuredClone(data);
  sample.items[0].zh = "测试</script>测试";
  const page = makePage(sample);
  assert.equal((page.match(/<\/script>/g)||[]).length,3);
  assert.ok(!page.includes("@@MODEL@@"));
});

test("paired synthetic benchmark is reproducible and counts each learner once", () => {
  const { simulate } = require("../tools/cat/benchmark.cjs");
  const study = () => simulate(data, [80,150], 3, 110, 20261125);
  const output = study();
  assert.deepEqual(output, study());
  assert.equal(output.overall.participants,6);
  assert.equal(output.byLevel.length,2);
  assert.equal(output.overall.pairedAt25,6);
  assert.ok(output.overall.terminalRMSE >= 0 && Number.isFinite(output.overall.random25RMSE));
});
