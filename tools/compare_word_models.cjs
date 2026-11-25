// Offline comparison on the same reviewed cues. The production player is not modified.
const { evaluate, labelSpan, spanKey, load } = require("./evaluate_prediction.cjs");
const { classify, markKey, preferences } = require("../app/web/assets/prediction.js");

const { createHash } = require("node:crypto");
const { readFileSync } = require("node:fs");

const thresholds = { more: 0.3, normal: 0.5, less: 0.7 };

function sigmoid(logit) {
  const small = Math.exp(-Math.abs(logit));
  return logit >= 0 ? 1 / (1 + small) : small / (1 + small);
}

// Invert the expected vocabulary sum to put an old level estimate on this particular table's scale.
function abilityForLevel(difficulties, level, a) {
  if (!(level > 0 && level < difficulties.length)) {
    throw new Error("词汇量须小于难度表条数，或在参数文件提供已测得的 theta");
  }
  const shift = Math.log(level / (difficulties.length - level)) / a;
  let low = Infinity, high = -Infinity;
  for (const b of difficulties) { low = Math.min(low, b); high = Math.max(high, b); }
  low += shift; high += shift;
  for (let i = 0; i < 60; i++) {
    const mid = (low + high) / 2;
    let expected = 0;
    for (const b of difficulties) expected += sigmoid(a * (mid - b));
    if (expected < level) low = mid;
    else high = mid;
  }
  return (low + high) / 2;
}

function probabilityMetrics(samples) {
  const bins = Array.from({ length: 10 }, (_, i) => ({ lower: i / 10, upper: (i + 1) / 10, count: 0, sum: 0, positive: 0 }));
  let loss = 0, positives = 0;
  for (const { logit, unknown } of samples) {
    const p = sigmoid(logit), bin = bins[Math.min(9, Math.floor(p * 10))];
    // Stable negative log likelihood, including probabilities numerically rounded to zero or one.
    loss += (Math.max(unknown ? -logit : logit, 0) + Math.log1p(Math.exp(-Math.abs(logit)))) / samples.length;
    positives += Number(unknown);
    bin.count++; bin.sum += p; bin.positive += Number(unknown);
  }
  const sorted = [...samples].sort((a, b) => a.logit - b.logit);
  let negativesBelow = 0, favorable = 0;
  for (let i = 0; i < sorted.length;) {
    let positive = 0, negative = 0;
    const score = sorted[i].logit;
    while (i < sorted.length && sorted[i].logit === score) {
      if (sorted[i++].unknown) positive++; else negative++;
    }
    favorable += positive * (negativesBelow + negative / 2);
    negativesBelow += negative;
  }
  return {
    observations: samples.length, positives,
    logLoss: samples.length ? loss : null,
    auc: positives && negativesBelow ? favorable / (positives * negativesBelow) : null,
    calibration: bins.filter(b => b.count).map(b => ({
      lower: b.lower, upper: b.upper, count: b.count,
      meanPredicted: b.sum / b.count, observedUnknown: b.positive / b.count,
    })),
  };
}

function compare(video, labels, profile, preset, table, config = {}, tableSha256) {
  const baseline = evaluate(video, labels, profile, preset);
  if (table?.version !== 1 || table.scale !== "natural-log lemma rank" || !table.words || Array.isArray(table.words) ||
      typeof table.words !== "object" || !Object.keys(table.words).length)
    throw new Error("需要 P1 生成的 v1 词难度表");
  const numeric = ["theta", "discrimination", "viewingDiscount", "domainBias"];
  if (!config || typeof config !== "object" || Array.isArray(config) || Object.keys(config).some(k =>
    numeric.includes(k) ? !Number.isFinite(config[k]) : !["thetaByField", "tableSha256"].includes(k)))
    throw new Error("IRT 参数名或数值无效");
  const { discrimination: a = 2, viewingDiscount = 0, domainBias = 0 } = config;
  if (a <= 0 || viewingDiscount < 0) throw new Error("区分度须为正，观看折扣不可为负");
  const calibrated = Object.hasOwn(config, "thetaByField") || Object.hasOwn(config, "tableSha256");
  if (calibrated) {
    const byField = config.thetaByField;
    if (config.theta !== undefined || !byField || typeof byField !== "object" || Object.keys(byField).length !== 2 ||
        ![byField.b_initial, byField.b_frequency].every(Number.isFinite))
      throw new Error("分量尺能力参数须同时提供两套 theta");
    if (typeof config.tableSha256 !== "string" || !/^[a-f0-9]{64}$/.test(config.tableSha256) ||
        tableSha256 !== config.tableSha256) throw new Error("难度表指纹与测量参数不一致");
  }
  const rows = Object.values(table.words);
  if (rows.some(r => !Number.isFinite(r?.b_frequency) || !Number.isFinite(r?.b_initial)))
    throw new Error("词难度表包含无效的难度");
  const known = new Set(profile.known), unknown = new Set(profile.unknown);
  const threshold = thresholds[preset];
  const oldState = note => classify(note, known, unknown, profile.level * preferences[preset].factor);
  const modeled = [], coverage = { classified: 0, profileOverride: 0, missingWord: 0, otherKind: 0 };
  video.cues.forEach((cue, i) => {
    const truth = new Set(labels.cues[i].unknown.map(label => spanKey(labelSpan(cue.text, label))));
    for (const note of cue.notes) {
      const key = markKey(note);
      if (known.has(key) || unknown.has(key)) coverage.profileOverride++;
      else if (note.kind !== "word") coverage.otherKind++;
      else if (!Object.hasOwn(table.words, note.key)) coverage.missingWord++;
      else {
        coverage.classified++;
        modeled.push({ note, row: table.words[note.key], truth: truth.has(spanKey(note)) });
      }
    }
  });
  const models = {};
  for (const field of ["b_frequency", "b_initial"]) {
    const theta = config.thetaByField?.[field] ?? config.theta ?? abilityForLevel(rows.map(row => row[field]), profile.level, a);
    if (!Number.isFinite(theta)) throw new Error("无法在当前尺度上计算有限的能力值");
    const decisions = new Map(), samples = [];
    for (const { note, row, truth } of modeled) {
      const logit = a * (row[field] - (theta + domainBias - viewingDiscount));
      if (!Number.isFinite(logit)) throw new Error("参数超出可计算的范围");
      decisions.set(note, sigmoid(logit) >= threshold ? "hard" : "easy");
      samples.push({ logit, unknown: truth });
    }
    const display = evaluate(video, labels, profile, preset, note => decisions.get(note) ?? oldState(note));
    models[field] = { theta, display, probability: probabilityMetrics(samples) };
  }
  return { baseline, coverage, params: { a, viewingDiscount, domainBias, threshold,
    abilitySource: calibrated ? "measured theta per table field" : config.theta === undefined ? "expected-vocabulary approximation" : "provided theta" }, models };
}

module.exports = { compare, abilityForLevel, probabilityMetrics };
if (require.main === module) {
  try {
    const [video, labels, profile, table, preset, config] = process.argv.slice(2);
    if (process.argv.length < 7 || process.argv.length > 8)
      throw new Error("用法：node tools/compare_word_models.cjs VIDEO LABELS PROFILE TABLE more|normal|less [PARAMETERS]");
    const bytes = readFileSync(table);
    const difficulty = JSON.parse(bytes.toString("utf8").replace(/^\uFEFF/, ""));
    const fingerprint = createHash("sha256").update(bytes).digest("hex");
    console.log(JSON.stringify(compare(load(video), load(labels), load(profile), preset, difficulty,
      config ? load(config) : {}, fingerprint), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
