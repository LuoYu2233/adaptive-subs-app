// Paired synthetic study. The same participant has the same potential answer to an item in both arms.
"use strict";
const fs = require("node:fs");
const { buildData } = require("../build_cat_pilot.cjs");
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
      let count = 0;
      const usedSets = new Set();
      for (const item of shuffled) {
        if (item.contrastSet && usedSets.has(item.contrastSet)) continue;
        if (item.contrastSet) usedSets.add(item.contrastSet);
        control.update(item.b_initial, correct.get(item.word));
        if (++count === 25) break;
      }
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
    const [table, flag] = process.argv.slice(2);
    if (!table || process.argv.length > 4 || (flag !== undefined && flag !== "--expanded"))
      throw new Error("用法：node tools/cat/benchmark.cjs TABLE.json [--expanded]");
    const data = buildData(fs.readFileSync(table), flag === "--expanded");
    console.log(JSON.stringify(simulate(data, [1500,3000,5000,8000,15000], 60, 5000, 20261125), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
