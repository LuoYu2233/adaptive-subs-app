// Draw only from authored four-way contrast sets. Preserve all original matched items.
"use strict";
const { createHash } = require("node:crypto");

function readSets(text) {
  const [header, ...lines] = text.replace(/^\uFEFF/, "").trimEnd().split(/\r?\n/);
  if (header !== "主题\t词义一\t词义二\t词义三\t词义四") throw new Error("对照词组表头无效");
  const seen = new Set();
  return lines.flatMap((line, index) => {
    const [context, ...cells] = line.split("\t");
    const pairs = cells.map(cell => cell.split("="));
    if (!context || pairs.length !== 4 || pairs.some(([w, zh, ...other]) => !/^[a-z]+$/.test(w) ||
        !zh?.trim() || zh.trim() !== zh || other.length) || new Set(pairs.map(row => row[1])).size !== 4)
      throw new Error(`第 ${index + 2} 行需要一个主题和四个互异的词义`);
    const contrastSet = pairs.map(([w]) => w).sort().join("/");
    return pairs.map(([word, zh]) => {
      if (seen.has(word)) throw new Error("重复词头：" + word);
      seen.add(word);
      return { word, zh, context, contrastSet, distractors: pairs.filter(row => row[0] !== word).map(row => row[1]) };
    });
  });
}

const bin = rank => Math.min(9, Math.max(0, Math.floor(10 * Math.log(rank / 300) / Math.log(25000 / 300))));
const hash = word => createHash("sha256").update("word-bank-v1/" + word).digest("hex");

function expand(table, original, text, target = 800) {
  if (table?.version !== 1 || table.scale !== "natural-log lemma rank" || !table.words) throw new Error("需要配套词难度表");
  const originals = new Set(original.bands.flatMap(b => b.words.map(r => r.word)).filter(w => Object.hasOwn(table.words, w)));
  const counts = Array(10).fill(0), pools = Array.from({ length: 10 }, () => []);
  const rankOf = w => table.words[w]?.rank;
  for (const word of originals) {
    const rank = rankOf(word);
    if (!Number.isFinite(rank) || rank < 1) throw new Error("词难度表缺少有效词频名次");
    counts[bin(rank)]++;
  }
  for (const item of readSets(text)) {
    const rank = rankOf(item.word);
    if (!originals.has(item.word) && rank >= 300 && rank <= 25000) pools[bin(rank)].push(item);
  }
  for (const pool of pools) {
    const ordered = pool.map(item => ({ item, key: hash(item.word) })).sort((a, b) => a.key < b.key ? -1 : 1);
    pool.splice(0, pool.length, ...ordered.map(row => row.item));
  }
  const extra = [];
  while (originals.size + extra.length < target) {
    let selected = -1;
    for (let i = 0; i < counts.length; i++) {
      if (pools[i].length && (selected < 0 || counts[i] < counts[selected])) selected = i;
    }
    if (selected < 0) throw new Error(`可匹配的四选一题不足 ${target} 道`);
    extra.push(pools[selected].pop());
    counts[selected]++;
  }
  return { variant: "expanded-v1", bands: [...original.bands, { words: extra }] };
}

module.exports = { readSets, expand, bin };
