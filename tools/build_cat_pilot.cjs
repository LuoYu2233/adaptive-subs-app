"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const here = name => path.join(__dirname, "cat", name);

function assemble(tableBytes, bank) {
  const table = JSON.parse(tableBytes.toString("utf8").replace(/^\uFEFF/, ""));
  if (table.version !== 1 || table.scale !== "natural-log lemma rank" || !table.words || Array.isArray(table.words))
    throw new Error("需要词难度表模块生成的 v1 表。");
  const difficulties = {};
  for (const field of ["b_initial", "b_frequency"]) {
    difficulties[field] = Object.values(table.words).map(row => row?.[field]);
    if (!difficulties[field].length || difficulties[field].some(n => !Number.isFinite(n) || n < 0 || n > 30))
      throw new Error("难度表的词难度超出有效范围。");
  }
  const items = [], seen = new Set();
  for (const band of bank.bands) {
    for (const { word, zh, distractors } of band.words) {
      if (!Object.hasOwn(table.words, word) || seen.has(word)) continue;
      if (!Array.isArray(distractors) || distractors.length !== 3 || new Set([zh, ...distractors]).size !== 4 ||
          [word, zh, ...distractors].some(t => typeof t !== "string" || !t.trim())) throw new Error("无效的四选一题目：" + word);
      const { b_initial, b_frequency } = table.words[word];
      items.push({word, zh, distractors, b_initial, b_frequency});
      seen.add(word);
    }
  }
  if (items.length < 40) throw new Error("题库与难度表匹配的有效题目少于 40 道。");
  return { items, difficulties, tableSha256: createHash("sha256").update(tableBytes).digest("hex") };
}

function makePage(data) {
  const content = JSON.stringify(data).replace(/</g, "\\u003c");
  const template = fs.readFileSync(here("pilot.html"), "utf8");
  const scripts = { DATA: content, MODEL: fs.readFileSync(here("model.js"), "utf8"), UI: fs.readFileSync(here("pilot.js"), "utf8") };
  return template.replace(/@@(DATA|MODEL|UI)@@/g, (_, name) => scripts[name]);
}

module.exports = { assemble, makePage };
if (require.main === module) {
  try {
    const [table, output] = process.argv.slice(2);
    if (process.argv.length !== 4) throw new Error("用法：node tools/build_cat_pilot.cjs TABLE.json OUTPUT.html");
    const bank = JSON.parse(fs.readFileSync(path.join(__dirname, "../app/data/vocab_test.json"), "utf8"));
    const data = assemble(fs.readFileSync(table), bank);
    fs.writeFileSync(output, makePage(data), { encoding: "utf8", flag: "wx" });
    console.log(`已生成 ${output}，可用单词题 ${data.items.length} 道。`);
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
