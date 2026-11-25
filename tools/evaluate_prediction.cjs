// Offline evaluation of annotations the shipped player actually displays.
const fs = require("node:fs");
const { createHash } = require("node:crypto");
const LabelWindow = require("../extension/label-window.js");
const { classify, preferences, selectNotes } = require("../app/web/assets/prediction.js");

function check(condition, message) {
  if (!condition) throw new Error(message);
}

function prepare(result, range) {
  const { video, cues, status } = result;
  check(status == null || status === "complete", "需要已完成的字幕");
  check(typeof video?.id === "string" && video.id.trim() && Number.isFinite(video.duration) && video.duration > 0,
    "视频缺少标识或有效时长");
  check(Array.isArray(cues) && cues.length, "视频没有字幕");
  const full = {
    version: 1, video: video.id, duration: video.duration,
    cues: cues.map(({ text, s, e, notes }, i) => {
      check(typeof text === "string" && Number.isFinite(s) && Number.isFinite(e) && s >= 0 && e > s &&
        (i === 0 || s >= cues[i - 1].s) && Array.isArray(notes), `第 ${i + 1} 句字幕无效`);
      const length = [...text].length;
      let previousEnd = 0;
      for (const n of notes) {
        check(n && Number.isInteger(n.s) && Number.isInteger(n.e) && n.s >= previousEnd && n.e > n.s &&
          n.e <= length && ["word", "phrase", "sense"].includes(n.kind) && typeof n.key === "string" &&
          n.key.trim() && Number.isFinite(n.lv) && n.lv > 0, `第 ${i + 1} 句候选词无效`);
        previousEnd = n.e;
      }
      return { s, e, text, unknown: null };
    }),
  };
  if (range === undefined) return full;
  const selected = LabelWindow.indices(cues, video.duration, range);
  return { ...full, version: 2, window: { start: range.start, end: range.end }, cues: selected.map(i => full.cues[i]) };
}

function labelSpan(sentence, label) {
  const term = typeof label === "string" ? label : label?.text;
  const occurrence = typeof label === "string" ? undefined : label?.occurrence;
  check(typeof term === "string" && term.trim().length > 0, "生词标注需要原文");
  if (typeof label === "object" && Object.hasOwn(label, "s")) {
    const points = [...sentence], s = label.s, e = s + [...term].length;
    check(occurrence === undefined && Number.isInteger(s) && s >= 0 && e <= points.length &&
      points.slice(s, e).join("") === term, "生词位置与原文不符");
    return { s, e };
  }
  check(occurrence === undefined || Number.isSafeInteger(occurrence) && occurrence > 0, "出现次数须为正整数");
  // Use literal, whole-word matches so "bank" cannot label the beginning of "banker".
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp("(?<![\\p{L}\\p{N}_])" + escaped + "(?![\\p{L}\\p{N}_])", "gu");
  const matches = [...sentence.matchAll(pattern)];
  check(matches.length, `句子中找不到“${term}”`);
  check(occurrence !== undefined || matches.length === 1, `“${term}”重复出现，请指定 occurrence`);
  const match = matches[(occurrence ?? 1) - 1];
  check(match, `句中没有第 ${occurrence} 个“${term}”`);
  const s = [...sentence.slice(0, match.index)].length;
  return { s, e: s + [...term].length };
}

const spanKey = ({ s, e }) => `${s}:${e}`;

function evaluate(result, labels, profile, preset, classifyNote) {
  const expected = prepare(result);
  const range = labels?.window;
  const selected = LabelWindow.indices(expected.cues, expected.duration, range);
  const duration = range === undefined ? expected.duration : range.end - range.start;
  check(labels?.version === (range === undefined ? 1 : 2) && labels.video === expected.video && labels.duration === expected.duration &&
    Array.isArray(labels.cues) && labels.cues.length === selected.length, "标注与视频不匹配");
  check(Number.isFinite(profile?.level) && profile.level > 0 &&
    [profile.known, profile.unknown].every(list => Array.isArray(list) && list.every(k => typeof k === "string")),
    "需要 level、known 和 unknown 的观看前画像快照");
  check(Object.hasOwn(preferences, preset), "注释档位须为 more、normal 或 less");
  const preference = preferences[preset];
  const known = new Set(profile.known), unknown = new Set(profile.unknown);
  const state = classifyNote ?? (note => classify(note, known, unknown, profile.level * preference.factor));
  const repetitions = new Map();
  let hits = 0, extra = 0, noCandidate = 0, belowThresholdOrKnown = 0, displayLimit = 0;

  let cursor = 0;
  expected.cues.forEach(({ s, e, text }, i) => {
    if (cursor === selected.length) return;
    const notes = result.cues[i].notes;
    const indices = selectNotes(notes, state, preference, repetitions);
    if (i !== selected[cursor]) return;  // Earlier displays consume repeats just as they do in the player.
    const answer = labels.cues[cursor++];
    check(answer?.text === text && answer.s === s && answer.e === e, `第 ${i + 1} 句与原字幕不匹配`);
    check(Array.isArray(answer.unknown), `第 ${i + 1} 句尚未标注；若全部认识请填 []`);
    const spans = answer.unknown.map(label => labelSpan(text, label)).sort((a, b) => a.s - b.s);
    check(spans.every((span, j) => !j || spans[j - 1].e <= span.s), `第 ${i + 1} 句含重复或交叠的标注`);
    const truth = new Set(spans.map(spanKey));
    const candidates = new Map(notes.map(note => [spanKey(note), note]));
    const displayed = new Set([...indices].map(index => spanKey(notes[index])));
    for (const key of displayed) {
      if (truth.has(key)) hits++;
      else extra++;
    }
    for (const key of truth) {
      if (displayed.has(key)) continue;
      const note = candidates.get(key);
      if (!note) noCandidate++;
      else if (["forced", "hard"].includes(state(note))) displayLimit++;
      else belowThresholdOrKnown++;
    }
  });
  const missed = noCandidate + belowThresholdOrKnown + displayLimit;
  return {
    video: expected.video, preset, durationSeconds: duration, evaluatedCues: selected.length,
    ...(range === undefined ? {} : { window: { start: range.start, end: range.end } }),
    ...displayMetrics(hits, missed, extra, duration),
    missedByReason: { noCandidate, belowThresholdOrKnown, displayLimit },
  };
}

function displayMetrics(hits, missed, extra, durationSeconds) {
  const denominator = 5 * hits + 4 * missed + extra;
  return {
    hits, missed, extra, missRate: hits + missed ? missed / (hits + missed) : null,
    extraPerMinute: extra * 60 / durationSeconds, f2: denominator ? 5 * hits / denominator : null,
  };
}

function load(file) {
  return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
}

function unpackBlindPacket(packet) {
  check(packet?.version === 1 && packet.kind === "blind-video-evaluation", "需要盲标导出的完整评分包");
  const { video, labels, profile, subtitleSha256, mediaSha256 } = packet;
  if (subtitleSha256 !== undefined) {
    check(Array.isArray(video?.cues), "评分包缺少字幕");
    const transcript = JSON.stringify(video.cues.map(c => [c.s, c.e, c.text]));
    check(createHash("sha256").update(transcript).digest("hex") === subtitleSha256, "评分包的英文字幕与指纹不一致");
  }
  if (mediaSha256 !== undefined) check(typeof mediaSha256 === "string" && /^[a-f0-9]{64}$/.test(mediaSha256), "视频指纹无效");
  return { video, labels, profile, mediaSha256, subtitleSha256 };
}

function main(args) {
  const [command, video, labels, profile, preset] = args;
  if (command === "prepare" && (args.length === 3 || args.length === 5)) {
    const range = args.length === 5 ? { start: LabelWindow.seconds(profile), end: LabelWindow.seconds(preset) } : undefined;
    const sheet = prepare(load(video), range);
    fs.writeFileSync(labels, JSON.stringify(sheet, null, 2) + "\n", { encoding: "utf8", flag: "wx" });
  } else if (command === "score-bundle" && args.length === 3) {
    const packet = unpackBlindPacket(load(video));
    console.log(JSON.stringify(evaluate(packet.video, packet.labels, packet.profile, labels), null, 2));
  } else if (command === "score" && args.length === 5) {
    console.log(JSON.stringify(evaluate(load(video), load(labels), load(profile), preset), null, 2));
  } else {
    throw new Error("用法：prepare VIDEO.json LABELS.json [START END] | score VIDEO.json LABELS.json PROFILE.json more|normal|less | score-bundle PACKET.json more|normal|less");
  }
}

module.exports = { prepare, evaluate, displayMetrics, labelSpan, spanKey, load, unpackBlindPacket };
if (require.main === module) {
  try { main(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
