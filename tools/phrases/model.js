"use strict";
const PhraseCat = (() => {
  const shared = typeof module === "undefined" ? WordCat : require("../cat/model.js");
  const scale = "phrase-pilot-five-levels-v1", priorSD = 1.2;
  const bFor = item => item.level - 3;

  class Session {
    constructor(bank, random = Math.random) {
      this.bank = bank;
      this.random = random;
      this.remaining = [...bank.items];
      this.history = [];
      this.post = new shared.Posterior(0, priorSD);
      this.current = null;
    }
    stopReason() {
      if (this.history.length >= 6 && this.post.se <= .45) return "precision";
      if (this.history.length >= 8) return "limit";
      return null;
    }
    next() {
      if (this.current || this.stopReason()) return this.current;
      const ranked = this.remaining.map((item, index) => ({ index, info: shared.information(this.post.theta, bFor(item)) }))
        .sort((a, b) => b.info - a.info);
      // All phrases in a provisional level tie. Give every tied item an equal chance at the chosen rank.
      const rank = Math.floor(this.random() * Math.min(5, ranked.length));
      const ties = ranked.filter(row => row.info === ranked[rank].info);
      const selected = ties[Math.floor(this.random() * ties.length)];
      const item = this.remaining.splice(selected.index, 1)[0];
      this.current = { item, choices: shared.shuffled([item.meaning, ...item.distractors], this.random) };
      return this.current;
    }
    answer(index) {
      if (!this.current || !(index === null || Number.isInteger(index) && index >= 0 && index < 4))
        throw new Error("请选择一个释义或不认识。");
      const { item, choices } = this.current;
      const selected = index === null ? null : choices[index], correct = selected === item.meaning;
      this.post.update(bFor(item), correct);
      this.history.push({ phrase: item.phrase, level: item.level, selected, correct });
      this.current = null;
    }
    result() {
      const stopReason = this.stopReason();
      if (!stopReason) throw new Error("请先完成短语试测。");
      return { version: 1, model: "3PL phrase CAT", scale, bankSha256: this.bank.bankSha256, priorSD,
        discrimination: 2, guessing: .10, stopReason, thetaPhrase: this.post.theta, se: this.post.se,
        responses: this.history.map(r => ({ ...r })) };
    }
  }
  return { scale, Session };
})();
if (typeof module !== "undefined") module.exports = PhraseCat;
