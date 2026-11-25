"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const source = name => fs.readFileSync(path.join(__dirname, name), "utf8");

function assemble(text) {
  const normalized = text.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").trimEnd() + "\n";
  const [header, ...rows] = normalized.trimEnd().split("\n");
  if (header !== "level\tphrase\tsentence\tmeaning\tdistractor1\tdistractor2\tdistractor3")
    throw new Error("短语题库表头无效");
  const seen = new Set();
  const items = rows.map((row, i) => {
    const [rawLevel, phrase, sentence, meaning, ...distractors] = row.split("\t");
    const level = Number(rawLevel);
    const marked = /^([^\[\]]*)\[([^\[\]]+)\]([^\[\]]*)$/.exec(sentence || "");
    if (!/^[1-5]$/.test(rawLevel) || !/^[a-z]+(?: [a-z]+)+$/.test(phrase || "") || seen.has(phrase) || !marked ||
        distractors.length !== 3 || [meaning, ...distractors].some(s => !s?.trim() || s !== s.trim()) ||
        new Set([meaning, ...distractors]).size !== 4)
      throw new Error(`第 ${i + 2} 行的短语、语境或选项无效`);
    seen.add(phrase);
    return { level, phrase, before: marked[1], target: marked[2], after: marked[3], meaning, distractors };
  });
  if ([1,2,3,4,5].some(level => items.filter(item => item.level === level).length < 8))
    throw new Error("每个难度层至少需要八道可选题");
  return { items, bankSha256: createHash("sha256").update(normalized).digest("hex") };
}

function makePage(bank) {
  const values = { DATA: JSON.stringify(bank).replace(/</g, "\\u003c"), MODEL: source("cat/model.js"),
    PHRASE: source("phrases/model.js"), UI: source("phrases/pilot.js") };
  return source("phrases/pilot.html").replace(/@@(DATA|MODEL|PHRASE|UI)@@/g, (_, key) => values[key]);
}

module.exports = { assemble, makePage };
if (require.main === module) {
  try {
    if (process.argv.length !== 3) throw new Error("用法：node tools/build_phrase_pilot.cjs OUTPUT.html");
    const bank = assemble(source("phrases/items.tsv"));
    fs.writeFileSync(process.argv[2], makePage(bank), { encoding: "utf8", flag: "wx" });
    console.log(`已生成短语试测页，共 ${bank.items.length} 道语境题。`);
  } catch (e) { console.error(e.message); process.exitCode = 1; }
}
