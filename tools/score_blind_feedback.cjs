// Evaluate a previously frozen feedback fit against independent blind word labels.
"use strict";
const path = require("node:path");
const { createHash } = require("node:crypto");
const { load, displayMetrics } = require("./evaluate_prediction.cjs");
const { compare, readTable, probabilityMetrics } = require("./compare_word_models.cjs");

function blindReport(fit, table, tableSha256, entries, preset) {
  if (fit?.version !== 1 || fit.tableSha256 !== tableSha256 || !["b_initial", "b_frequency"].includes(fit.field) ||
      !Number.isSafeInteger(fit.trainingCutoff) || fit.trainingCutoff < 0 || !Array.isArray(fit.trainingClips) ||
      !Array.isArray(fit.trainingSubtitleHashes) || !Array.isArray(entries) || !entries.length)
    throw new Error("请使用配套词表、冻结的校准报告和独立盲标评分包");
  const { anchor, estimate, field } = fit;
  if (!anchor || !estimate || ![anchor.theta, anchor.discrimination, estimate.theta, estimate.viewingDiscount].every(Number.isFinite) ||
      anchor.discrimination <= 0 || estimate.viewingDiscount < 0 || !estimate.domainBias ||
      typeof estimate.domainBias !== "object" || Array.isArray(estimate.domainBias) || !Object.values(estimate.domainBias).every(Number.isFinite))
    throw new Error("冻结模型的参数无效");
  if (fit.trainingSubtitleHashes.length !== fit.trainingClips.length) throw new Error("训练记录缺少字幕指纹，无法核验独立留出片段");
  const usedClips = new Set(fit.trainingClips), usedHashes = new Set(fit.trainingSubtitleHashes);
  const videos = new Set();
  const scored = entries.map(({ clip, domain, packet }) => {
    if (![clip, domain].every(s => typeof s === "string" && s.trim() && s === s.trim()) || !packet ||
        packet.version !== 1 || packet.kind !== "blind-video-evaluation" || !packet.sourceVerifiedAtStart)
      throw new Error("需要匿名代号、领域以及已核验原始候选的完整盲标评分包");
    const collection = packet.labels?.collection;
    if (collection?.mode !== "blind-cue" || !Number.isSafeInteger(collection.startedAt) ||
        !Number.isSafeInteger(collection.completedAt) || collection.completedAt < collection.startedAt ||
        collection.startedAt < fit.trainingCutoff) throw new Error("盲标必须在训练结束后独立开始并全部确认");
    const digest = createHash("sha256").update(JSON.stringify(packet.video.cues.map(c => [c.s,c.e,c.text]))).digest("hex");
    if (digest !== packet.subtitleSha256) throw new Error("评分包的英文字幕与指纹不一致");
    const videoId = packet.video.video?.id;
    if (usedClips.has(clip) || usedHashes.has(digest) || videos.has(videoId))
      throw new Error("训练或留出集中重复使用视频、代号或字幕");
    usedClips.add(clip); usedHashes.add(digest); videos.add(videoId);
    function run(theta, domainBias, viewingDiscount) {
      let samples;
      const r = compare(packet.video,packet.labels,packet.profile,preset,table,
        {theta, discrimination: anchor.discrimination, domainBias, viewingDiscount},
        {tableSha256, onSamples: (f, values) => { if (f === field) samples = values; }});
      return { baseline: r.baseline, coverage: r.coverage, overlappingLabels: r.overlappingLabels,
        model: r.models[field], samples };
    }
    const bias = Object.hasOwn(estimate.domainBias, domain) ? estimate.domainBias[domain] : 0;
    const before = run(anchor.theta, 0, 0), after = run(estimate.theta, bias, estimate.viewingDiscount);
    return { clip, domain, before, after };
  });
  function pooledDisplay(rows) {
    const sum = key => rows.reduce((s, r) => s + r[key], 0);
    const durationSeconds = sum("durationSeconds"), hits = sum("hits"), missed = sum("missed"), extra = sum("extra");
    const missedByReason = {};
    for (const reason of ["noCandidate","belowThresholdOrKnown","displayLimit"])
      missedByReason[reason] = rows.reduce((n, r) => n + r.missedByReason[reason], 0);
    return { durationSeconds, hits, missed, extra, missedByReason, ...displayMetrics(hits, missed, extra, durationSeconds) };
  }
  const pooled = side => ({ display: pooledDisplay(scored.map(s => s[side].model.display)),
    probability: probabilityMetrics(scored.flatMap(s => s[side].samples)) });
  return { version: 1, field, tableSha256, preset, trainingCutoff: fit.trainingCutoff,
    legacy: pooledDisplay(scored.map(s => s.before.baseline)), anchor: pooled("before"), updated: pooled("after"),
    clips: scored.map(({ clip, domain, before, after }) => ({ clip, domain, coverage: before.coverage,
      overlappingLabels: before.overlappingLabels, legacy: before.baseline, anchor: before.model, updated: after.model })) };
}

module.exports = { blindReport };
if (require.main === module) {
  try {
    const [modelFile, tableFile, manifestFile, preset] = process.argv.slice(2);
    if (process.argv.length !== 6) throw new Error("用法：node tools/score_blind_feedback.cjs MODEL TABLE MANIFEST more|normal|less");
    const manifest = load(manifestFile), dir = path.dirname(path.resolve(manifestFile));
    if (manifest?.version !== 1 || !Array.isArray(manifest.clips)) throw new Error("需要包含 clips 的 v1 盲标清单");
    const entries = manifest.clips.map(r => ({ ...r, packet: load(path.resolve(dir, r.packet)) }));
    const { table, tableSha256 } = readTable(tableFile);
    console.log(JSON.stringify(blindReport(load(modelFile), table, tableSha256, entries, preset), null, 2));
  } catch (e) { console.error(e.message); process.exitCode = 1; }
}
