// Compare the same paired predictions for a cohort, weighting counts and duration across sessions.
"use strict";
const path = require("node:path");
const { load, displayMetrics, unpackBlindPacket } = require("./evaluate_prediction.cjs");
const { readTable, compare, probabilityMetrics } = require("./compare_word_models.cjs");

function summarize(sessions) {
  const durationSeconds = sessions.reduce((n, row) => n + row.report.baseline.durationSeconds, 0);
  const result = { sessions: sessions.length, durationSeconds, overlappingLabels: 0, overrideSources: {}, coverage: {}, models: {} };
  for (const { report } of sessions) {
    result.overlappingLabels += report.overlappingLabels;
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

// Keep all of one learner's videos together when checking whether the aggregate depends on that learner.
function pairedChanges(overall, learners) {
  const fields = ["baseline","b_frequency","b_initial"];
  const metrics = ["missRate","extraPerMinute","f2"];
  const differences = scores => Object.fromEntries(fields.slice(1).map(field => [field,
    Object.fromEntries(metrics.map(key => [key, scores[field][key] == null || scores.baseline[key] == null
      ? null : scores[field][key] - scores.baseline[key]]))]));
  const all = Object.fromEntries(fields.map(field => [field, overall.models[field].display]));
  const changes = differences(all);
  const leaveOneLearnerOut = [];
  if (learners.length > 1) {
    for (const person of learners) {
      const remaining = Object.fromEntries(fields.map(field => {
        const drop = person.models[field].display, total = all[field];
        return [field, displayMetrics(total.hits - drop.hits, total.missed - drop.missed, total.extra - drop.extra,
          overall.durationSeconds - person.durationSeconds)];
      }));
      leaveOneLearnerOut.push({ omitted: person.learner, changes: differences(remaining) });
    }
  }
  return { changes, leaveOneLearnerOut };
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
  const seen = new Map(), mediaByVideo = new Map();
  const sessions = manifest.sessions.map((row, index) => {
    try {
      if (![row?.learner, row?.domain].every(s => typeof s === "string" && s.trim() && s === s.trim()))
        throw new Error("需要匿名学习者代号与片段领域");
      let session;
      if (row.packet !== undefined) {
        if (["video","labels","profile"].some(k => row[k] !== undefined))
          throw new Error("同一场请选择完整评分包或三个单独文件");
        session = unpackBlindPacket(read(row.packet));
      } else {
        session = { video: read(row.video), labels: read(row.labels), profile: read(row.profile) };
      }
      const { video, labels, profile, mediaSha256, subtitleSha256 } = session, samples = {};
      const report = compare(video, labels, profile, preset, table,
        row.parameters === undefined ? {} : read(row.parameters),
        { tableSha256, onSamples: (field, values) => { samples[field] = values; } });
      const videoId = video.video.id;
      if (mediaSha256) {
        if (mediaByVideo.has(videoId) && mediaByVideo.get(videoId) !== mediaSha256)
          throw new Error("同一字幕视频标识对应了不同平台视频");
        mediaByVideo.set(videoId, mediaSha256);
      }
      return { learner: row.learner, domain: row.domain, videoId, subtitleSha256, report, samples };
    } catch (error) { throw new Error(`第 ${index + 1} 组会话：${error.message}`); }
  });
  const identity = s => mediaByVideo.get(s.videoId) ? "media:" + mediaByVideo.get(s.videoId) : "id:" + s.videoId;
  sessions.forEach((s, i) => {
    const id = JSON.stringify([s.learner, identity(s)]);
    const ranges = seen.get(id) || [];
    const baseline = s.report.baseline;
    const range = baseline.window ?? { start: 0, end: baseline.durationSeconds };
    if (ranges.some(r => r.start < range.end && range.start < r.end ||
        r.hash && s.subtitleSha256 && r.hash !== s.subtitleSha256))
      throw new Error(`第 ${i + 1} 组会话：同一视频的片段重复、时间重叠或字幕轨不一致`);
    ranges.push({ ...range, hash: s.subtitleSha256 });
    seen.set(id, ranges);
  });
  const overall = summarize(sessions), byLearner = group(sessions, "learner");
  return {
    preset, tableSha256, participants: byLearner.length, videos: new Set(sessions.map(identity)).size, overall,
    byLearner, byDomain: group(sessions, "domain"), pairedDisplay: pairedChanges(overall, byLearner),
  };
}

module.exports = { cohort };
if (require.main === module) {
  try {
    if (process.argv.length !== 5) throw new Error("用法：node tools/compare_word_cohort.cjs MANIFEST TABLE more|normal|less");
    console.log(JSON.stringify(cohort(...process.argv.slice(2)), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
