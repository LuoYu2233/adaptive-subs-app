// Compare the same paired predictions for a cohort, weighting counts and duration across sessions.
"use strict";
const path = require("node:path");
const { load, displayMetrics } = require("./evaluate_prediction.cjs");
const { readTable, compare, probabilityMetrics } = require("./compare_word_models.cjs");

function summarize(sessions) {
  const durationSeconds = sessions.reduce((n, row) => n + row.report.baseline.durationSeconds, 0);
  const result = { sessions: sessions.length, durationSeconds, overrideSources: {}, coverage: {}, models: {} };
  for (const { report } of sessions) {
    const source = report.params.overrideSource;
    result.overrideSources[source] = (result.overrideSources[source] || 0) + 1;
    for (const [key, count] of Object.entries(report.coverage))
      result.coverage[key] = (result.coverage[key] || 0) + count;
  }
  for (const field of ["baseline", "b_frequency", "b_initial"]) {
    let hits = 0, missed = 0, extra = 0;
    const reasons = { noCandidate: 0, belowThresholdOrKnown: 0, displayLimit: 0 };
    for (const { report } of sessions) {
      const d = field === "baseline" ? report.baseline : report.models[field].display;
      hits += d.hits; missed += d.missed; extra += d.extra;
      for (const key of Object.keys(reasons)) reasons[key] += d.missedByReason[key];
    }
    const model = { display: { ...displayMetrics(hits, missed, extra, durationSeconds), missedByReason: reasons } };
    if (field !== "baseline") model.probability = probabilityMetrics(sessions.flatMap(row => row.samples[field]));
    result.models[field] = model;
  }
  return result;
}

function group(sessions, key) {
  const groups = new Map();
  for (const row of sessions) {
    if (!groups.has(row[key])) groups.set(row[key], []);
    groups.get(row[key]).push(row);
  }
  return [...groups].map(([name, rows]) => ({ [key]: name, ...summarize(rows) }));
}

function cohort(manifestPath, tableFile, preset) {
  const manifest = load(manifestPath), { table, tableSha256 } = readTable(tableFile);
  if (manifest?.version !== 1 || !Array.isArray(manifest.sessions) || !manifest.sessions.length)
    throw new Error("需要 v1 研究清单和至少一组会话");
  const directory = path.dirname(path.resolve(manifestPath));
  const read = file => {
    if (typeof file !== "string" || !file.trim()) throw new Error("缺少本地文件路径");
    return load(path.resolve(directory, file));
  };
  const seen = new Set();
  const sessions = manifest.sessions.map((row, index) => {
    try {
      if (![row?.learner, row?.domain].every(s => typeof s === "string" && s.trim() && s === s.trim()))
        throw new Error("需要匿名学习者代号与片段领域");
      const video = read(row.video), samples = {};
      const id = JSON.stringify([row.learner, video.video?.id]);
      if (seen.has(id)) throw new Error("同一学习者和视频重复列入");
      seen.add(id);
      const report = compare(video, read(row.labels), read(row.profile), preset, table,
        row.parameters === undefined ? {} : read(row.parameters),
        { tableSha256, onSamples: (field, values) => { samples[field] = values; } });
      return { learner: row.learner, domain: row.domain, report, samples };
    } catch (error) { throw new Error(`第 ${index + 1} 组会话：${error.message}`); }
  });
  return {
    preset, tableSha256, participants: new Set(sessions.map(r => r.learner)).size,
    videos: new Set(sessions.map(r => r.report.baseline.video)).size, overall: summarize(sessions),
    byLearner: group(sessions, "learner"), byDomain: group(sessions, "domain"),
  };
}

module.exports = { cohort };
if (require.main === module) {
  try {
    if (process.argv.length !== 5) throw new Error("用法：node tools/compare_word_cohort.cjs MANIFEST TABLE more|normal|less");
    console.log(JSON.stringify(cohort(...process.argv.slice(2)), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
