// Paired synthetic study. The same participant has the same potential answer to an item in both arms.
"use strict";
const fs = require("node:fs");
const { assemble } = require("../build_cat_pilot.cjs");
const { Session, Posterior, correctProbability, priorMean } = require("./model.js");

function simulate(data, levels, perLevel, initialLevel, seed) {
  let state = seed;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
  const records = [];
  for (const level of levels) {
    const truth = priorMean(data.difficulties.b_initial, level);
    for (let i = 0; i < perLevel; i++) {
      const correct = new Map(data.items.map(q => [q.word, random() < correctProbability(truth, q.b_initial)]));
      const session = new Session(data, initialLevel, random);
      const control = new Posterior(session.post.b_initial.theta);
      const shuffled = [...data.items];
      for (let j = shuffled.length - 1; j > 0; j--) {
        const k = Math.floor(random() * (j + 1));
        [shuffled[k], shuffled[j]] = [shuffled[j], shuffled[k]];
      }
      for (const item of shuffled.slice(0, 25)) control.update(item.b_initial, correct.get(item.word));
      let adaptive25 = null;
      while (session.next()) {
        const { item, choices } = session.current;
        session.answer(correct.get(item.word) ? choices.indexOf(item.zh) : null);
        if (session.history.length === 25) adaptive25 = (session.post.b_initial.theta - truth) ** 2;
      }
      records.push({ level, length: session.history.length, stop: session.stopReason(), adaptive25,
        random25: (control.theta - truth) ** 2, terminal: (session.post.b_initial.theta - truth) ** 2,
        se: session.post.b_initial.se });
    }
  }
  function summarize(rows) {
    const paired = rows.filter(r => r.adaptive25 !== null);
    const mean = (set, key) => set.reduce((s, r) => s + r[key], 0) / set.length;
    return {
      participants: rows.length, atLimit: rows.filter(r => r.stop === "limit").length,
      meanLength: mean(rows, "length"), pairedAt25: paired.length,
      adaptive25RMSE: paired.length ? Math.sqrt(mean(paired, "adaptive25")) : null,
      random25RMSE: paired.length ? Math.sqrt(mean(paired, "random25")) : null,
      terminalRMSE: Math.sqrt(mean(rows, "terminal")), meanSE: mean(rows, "se"),
    };
  }
  return { seed, initialLevel, tableSha256: data.tableSha256, overall: summarize(records),
    byLevel: levels.map(level => ({ level, ...summarize(records.filter(r => r.level === level)) })) };
}

module.exports = { simulate };
if (require.main === module) {
  try {
    if (process.argv.length !== 3) throw new Error("用法：node tools/cat/benchmark.cjs word-difficulty-v1.json");
    const bank = require("../../app/data/vocab_test.json");
    const data = assemble(fs.readFileSync(process.argv[2]), bank);
    console.log(JSON.stringify(simulate(data, [1500,3000,5000,8000,15000], 60, 5000, 20261125), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
