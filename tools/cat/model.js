// Word-only research CAT on the natural-log rank scale. All probabilities are local.
"use strict";
const WordCat = (() => {
  const fields = ["b_initial", "b_frequency"];
  const slope = 2, guess = 0.10, priorSD = 0.8;
  const logistic = x => 1 / (1 + Math.exp(-x));

  function expectedVocabulary(difficulties, theta) {
    return difficulties.reduce((sum, b) => sum + logistic(slope * (theta - b)), 0);
  }

  function priorMean(difficulties, level) {
    if (!(Number.isFinite(level) && level > 0 && level < difficulties.length))
      throw new Error("起始词汇量须大于零且小于难度表条数。");
    const midpoint = Math.log(level / (difficulties.length - level)) / slope;
    let low = Infinity, high = -Infinity;
    for (const b of difficulties) { low = Math.min(low, b); high = Math.max(high, b); }
    low += midpoint;
    high += midpoint;
    for (let i = 0; i < 48; i++) {
      const mid = (low + high) / 2;
      if (expectedVocabulary(difficulties, mid) < level) low = mid;
      else high = mid;
    }
    return (low + high) / 2;
  }

  function correctProbability(theta, b) {
    return guess + (1 - guess) * logistic(slope * (theta - b));
  }

  function information(theta, b) {
    const q = logistic(slope * (theta - b)), p = guess + (1 - guess) * q;
    // Cancel a factor of (1 - p) to avoid division by zero for very easy items.
    return slope ** 2 * q ** 2 * (1 - p) / p;
  }

  class Posterior {
    constructor(mean) {
      this.points = Array.from({length: 513}, (_, i) => mean + (i - 256) * 0.025);
      this.weights = this.points.map(t => Math.exp(-0.5 * ((t - mean) / priorSD) ** 2));
      this.summarize();
    }
    summarize() {
      const sum = this.weights.reduce((a, b) => a + b, 0);
      this.weights = this.weights.map(w => w / sum);
      this.theta = this.points.reduce((s, t, i) => s + t * this.weights[i], 0);
      this.se = Math.sqrt(this.points.reduce((s, t, i) => s + (t - this.theta) ** 2 * this.weights[i], 0));
    }
    update(b, correct) {
      this.weights = this.weights.map((w, i) => {
        const x = slope * (this.points[i] - b);
        return w * (correct ? guess + (1 - guess) * logistic(x) : (1 - guess) * logistic(-x));
      });
      this.summarize();
    }
  }

  function shuffled(values, random) {
    const result = [...values];
    for (let i = result.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [result[i], result[j]] = [result[j], result[i]];
    }
    return result;
  }

  class Session {
    constructor(data, level, random = Math.random) {
      this.data = data;
      this.level = level;
      this.random = random;
      this.post = Object.fromEntries(fields.map(f => [f, new Posterior(priorMean(data.difficulties[f], level))]));
      this.remaining = [...data.items];
      this.history = [];
      this.current = null;
    }
    stopReason() {
      if (this.history.length >= 12 && this.post.b_initial.se < 0.16) return "precision";
      if (this.history.length >= 40) return "limit";
      if (!this.remaining.length && !this.current) return "bank";
      return null;
    }
    next() {
      if (this.current || this.stopReason()) return this.current;
      const theta = this.post.b_initial.theta;
      const ranked = this.remaining.map((q, i) => ({ i, info: information(theta, q.b_initial) }))
        .sort((a, b) => b.info - a.info);
      const index = ranked[Math.floor(this.random() * Math.min(ranked.length, 5))].i;
      const item = this.remaining.splice(index, 1)[0];
      this.current = { item, choices: shuffled([item.zh, ...item.distractors], this.random) };
      return this.current;
    }
    answer(choice) {
      if (!this.current || !(choice === null || Number.isInteger(choice) && choice >= 0 && choice < 4))
        throw new Error("请选择一个释义或“不认识”。");
      const { item, choices } = this.current;
      const selected = choice === null ? null : choices[choice], correct = selected === item.zh;
      for (const field of fields) this.post[field].update(item[field], correct);
      this.history.push({ word: item.word, selected, correct });
      this.current = null;
    }
    result() {
      const reason = this.stopReason();
      if (!reason) throw new Error("请先完成本轮测量。");
      const fits = {}, thetaByField = {};
      for (const field of fields) {
        const { theta, se } = this.post[field];
        fits[field] = { theta, se, expectedVocabulary: Math.round(expectedVocabulary(this.data.difficulties[field], theta)) };
        thetaByField[field] = theta;
      }
      const parameters = { thetaByField, tableSha256: this.data.tableSha256, discrimination: slope };
      return { version: 1, model: "3PL word CAT", selectedBy: "b_initial", priorLevel: this.level, priorSD,
        guessing: guess, stopReason: reason, fits, responses: [...this.history], parameters };
    }
  }

  return { Session, correctProbability, information, expectedVocabulary, priorMean };
})();
if (typeof module !== "undefined") module.exports = WordCat;
