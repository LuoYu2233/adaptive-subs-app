/* Versioned, lossless wordbook backups. Shared by the player and offline tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AdaptiveWordbook = api;
})(globalThis, function () {
  "use strict";
  const MAX_ENTRIES = 10000, MAX_FILE_BYTES = 8 * 1024 * 1024;
  const fail = message => { throw new Error(message); };
  function text(value, name, limit, empty = false) {
    if (typeof value !== "string" || (!empty && !value.trim()) || value.length > limit || /[\uD800-\uDFFF]/u.test(value))
      fail(`生词本中的${name}无效`);
    return value;
  }
  function entries(value) {
    if (!Array.isArray(value) || value.length > MAX_ENTRIES) fail(`生词本最多支持 ${MAX_ENTRIES} 条记录`);
    const keys = new Set();
    return value.map(item => {
      if (!item || typeof item !== "object" || Array.isArray(item)) fail("生词本记录格式无效");
      const record = {
        key: text(item.key, "标识", 500), term: text(item.term, "词语", 500),
        zh: text(item.zh, "释义", 500, true), sentence: text(item.sentence, "原句", 10000, true),
        vid: text(item.vid, "视频标识", 120), kind: item.kind, lv: item.lv, t: item.t, added: item.added,
      };
      if (!/^[A-Za-z0-9_-]+$/.test(record.vid) || !["word", "phrase", "sense"].includes(record.kind)
          || ![record.lv, record.t].every(n => typeof n === "number" && Number.isFinite(n) && n >= 0)
          || !Number.isSafeInteger(record.added) || record.added < 0) fail("生词本记录的类型或时间无效");
      if (item.mk !== undefined) record.mk = text(item.mk, "词义标识", 500);
      if (item.hasNote !== undefined) {
        if (typeof item.hasNote !== "boolean") fail("生词本注释状态无效");
        record.hasNote = item.hasNote;
      }
      if (keys.has(record.key)) fail("备份中存在重复的生词标识");
      keys.add(record.key);
      return record;
    });
  }
  function backup(book) {
    return { format: "adaptive-subs-wordbook", version: 1, exported_at: new Date().toISOString(), entries: entries(book) };
  }
  function parse(value) {
    if (!value || value.format !== "adaptive-subs-wordbook" || value.version !== 1) fail("不是支持的生词本备份，请选择版本 1 的 JSON 文件");
    return entries(value.entries);
  }
  function merge(current, incoming) {
    const result = entries(current), keys = new Set(result.map(item => item.key));
    for (const item of entries(incoming)) {
      if (!keys.has(item.key)) { result.push(item); keys.add(item.key); }
    }
    if (result.length > MAX_ENTRIES) fail(`合并后超过 ${MAX_ENTRIES} 条，请先整理生词本`);
    return result;
  }
  function csvCell(value) {
    let s = String(value ?? "");
    // Spreadsheet programs may execute formulas even inside quoted CSV cells.
    if (/^[\s\uFEFF]*[=+\-@]/.test(s) || /^[\t\r\n]/.test(s)) s = "'" + s;
    return `"${s.replace(/"/g, '""')}"`;
  }
  return { MAX_ENTRIES, MAX_FILE_BYTES, entries, backup, parse, merge, csvCell };
});
