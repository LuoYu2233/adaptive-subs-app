const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { prepare, evaluate } = require("../tools/evaluate_prediction.cjs");

const profile = { level: 5000, known: [], unknown: [] };
const clip = { video: { id: "demo", duration: 120 }, cues: [
  { s: 0, e: 3, text: "😀 bank shore drifts.", notes: [
    { s: 2, e: 6, key: "bank", kind: "sense", lv: 8000 },
    { s: 7, e: 12, key: "shore", kind: "word", lv: 7000 },
  ] },
  { s: 5, e: 8, text: "shore quiet", notes: [
    { s: 0, e: 5, key: "shore", kind: "word", lv: 7000 },
    { s: 6, e: 11, key: "quiet", kind: "word", lv: 1000 },
  ] },
  { s: 10, e: 12, text: "shore", notes: [{ s: 0, e: 5, key: "shore", kind: "word", lv: 7000 }] },
] };

function answered() {
  const sheet = prepare(clip);
  sheet.cues[0].unknown = ["shore", "drifts"];
  sheet.cues[1].unknown = ["shore", "quiet"];
  sheet.cues[2].unknown = ["shore"];
  return sheet;
}

test("scores actual displays across cues and includes unknowns omitted by extraction", () => {
  const scores = evaluate(clip, answered(), profile, "normal");
  assert.deepEqual(scores, {
    video: "demo", preset: "normal", durationSeconds: 120, evaluatedCues: 3, hits: 2, missed: 3, extra: 1,
    missedByReason: { noCandidate: 1, belowThresholdOrKnown: 1, displayLimit: 1 },
    missRate: 3 / 5, extraPerMinute: 0.5, f2: 10 / 23,
  });
  const forced = evaluate(clip, answered(), { ...profile, unknown: ["shore", "quiet"], known: ["sense:bank"] }, "normal");
  assert.equal(forced.hits, 4);
  assert.equal(forced.extra, 0);
  assert.equal(evaluate(clip, answered(), profile, "more").missedByReason.displayLimit, 0);
});

test("blind sheet omits hints and needs an explicit answer for every cue", () => {
  const sheet = prepare(clip);
  assert.deepEqual(Object.keys(sheet.cues[0]).sort(), ["e", "s", "text", "unknown"]);
  assert.throws(() => evaluate(clip, sheet, profile, "normal"), /尚未标注/);
  for (const cue of sheet.cues) cue.unknown = [];
  const scores = evaluate(clip, sheet, { ...profile, known: ["sense:bank", "shore"] }, "normal");
  assert.equal(scores.missRate, null);
  assert.equal(scores.f2, null);
  assert.equal(scores.extraPerMinute, 0);
});

test("resolves repeated words by occurrence and phrases by their entire span", () => {
  const example = { video: clip.video, cues: [{ s: 0, e: 2, text: "banker bank bank take off C++", notes: [
    { s: 12, e: 16, key: "bank", kind: "word", lv: 7000 },
    { s: 17, e: 25, key: "take off", kind: "phrase", lv: 7000 },
    { s: 26, e: 29, key: "c++", kind: "word", lv: 7000 },
  ] }] };
  const sheet = prepare(example);
  const score = () => evaluate(example, sheet, profile, "normal");
  sheet.cues[0].unknown = ["bank"];
  assert.throws(score, /重复出现/);
  sheet.cues[0].unknown = [{ text: "bank", occurrence: 2 }, "take off", "C++"];
  assert.equal(score().hits, 3);
  sheet.cues[0].unknown = ["take off", "off"];
  assert.throws(score, /交叠/);
  sheet.cues[0].unknown = [{ text: "bank", occurrence: 3 }];
  assert.throws(score, /句中没有/);
});

test("rejects stale, partial and malformed inputs before scoring", () => {
  const sheet = answered();
  assert.throws(() => evaluate({ ...clip, status: "partial" }, sheet, profile, "normal"), /已完成/);
  assert.throws(() => evaluate(clip, sheet, {}, "normal"), /快照/);
  assert.throws(() => evaluate(clip, sheet, profile, "default"), /档位/);
  const corrupt = structuredClone(clip);
  corrupt.cues[1].notes[0].e = 90;
  assert.throws(() => prepare(corrupt), /候选词/);
  assert.throws(() => evaluate(clip, { ...sheet, video: "other" }, profile, "more"), /不匹配/);
  sheet.cues[1].s = 4;
  assert.throws(() => evaluate(clip, sheet, profile, "more"), /不匹配/);
});

test("CLI supports UTF-8 BOM and preserves an existing label file", t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "词汇评估-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const tool = path.join(__dirname, "../tools/evaluate_prediction.cjs");
  const run = args => spawnSync(process.execPath, [tool, ...args], { cwd: dir, encoding: "utf8" });
  const write = (name, value) => fs.writeFileSync(path.join(dir, name), "\uFEFF" + JSON.stringify(value), "utf8");
  write("video.json", clip); write("profile.json", profile);
  const args = ["prepare", "video.json", "标注.json"];
  assert.equal(run(args).status, 0);
  write("标注.json", answered());
  assert.notEqual(run(args).status, 0);
  const scored = run(["score", "video.json", "标注.json", "profile.json", "normal"]);
  assert.equal(scored.status, 0, scored.stderr);
  assert.equal(JSON.parse(scored.stdout).missed, 3);
});
