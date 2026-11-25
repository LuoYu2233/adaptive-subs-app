"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
const {prepare} = require("../tools/evaluate_prediction.cjs");
const {blindReport} = require("../tools/score_blind_feedback.cjs");

const hash = "a".repeat(64), field = "b_initial";
const fit = {version:1, field, tableSha256:hash, trainingCutoff:300, frozenAt:305, trainingMediaHashes:["c".repeat(64)], trainingClips:["training"], trainingSubtitleHashes:["b".repeat(64)],
  anchor:{theta:3,discrimination:2,measuredAt:100},estimate:{theta:1,viewingDiscount:.1,domainBias:{"科技":.2}}};
const table = {version:1,scale:"natural-log lemma rank",words:{orb:{b_initial:2,b_frequency:1}}};
function entry(name, s=0) {
  const video = {video:{id:name,duration:10}, cues:[{s,e:s+2,text:"orb",notes:[{s:0,e:3,key:"orb",kind:"word",lv:100}]}]};
  const labels = prepare(video); labels.cues[0].unknown = ["orb"];
  labels.collection = {mode:"blind-cue",startedAt:310,completedAt:370};
  const packet = {version:1,kind:"blind-video-evaluation",video,labels,profile:{level:3000,known:[],unknown:[],marks:{known:[],unknown:[]}},
    sourceVerifiedAtStart:true,mediaSha256:createHash("sha256").update(name).digest("hex"),subtitleSha256:createHash("sha256").update(JSON.stringify([[s,s+2,"orb"]])).digest("hex")};
  return {clip:name,domain:"科技",packet};
}
const score = (entries, f=fit) => blindReport(f,table,hash,entries,"normal");
test("frozen next-video score tests what was truly unknown, preserves the live cap and pools counts", () => {
  const result = score([entry("a"), {...entry("b",2),domain:"未见领域"}]);
  assert.equal(result.legacy.missed,2);
  assert.equal(result.anchor.display.missed,2);
  assert.equal(result.updated.display.hits,2);
  assert.equal(result.updated.probability.observations,2);
  assert.ok(result.updated.probability.logLoss < result.anchor.probability.logLoss);
  assert.equal(result.updated.display.durationSeconds,20);
  assert.ok(result.clips[1].updated.probability.logLoss < result.clips[0].updated.probability.logLoss);
});
test("rejects early answers, duplicate subtitles under new names, changed transcripts and missing provenance", () => {
  let e=entry("new");
  assert.throws(()=>score([e],{...fit,trainingSubtitleHashes:[]}),/训练记录缺少/);
  assert.throws(()=>score([e],{...fit,trainingSubtitleHashes:[e.packet.subtitleSha256]}),/重复/);
  assert.throws(()=>score([e,entry("alias")]),/重复/);
  assert.throws(()=>score([e],{...fit,trainingMediaHashes:[e.packet.mediaSha256]}),/重复/);
  assert.throws(()=>score([e],{...fit,frozenAt:undefined}),/冻结/);
  e.packet.labels.collection.startedAt=301;
  assert.throws(()=>score([e]),/冻结/);
  e.packet.labels.collection.startedAt=290;
  assert.throws(()=>score([e]),/训练结束/);
  e=entry("changed"); e.packet.video.cues[0].text="fog";
  assert.throws(()=>score([e]),/指纹/);
  e=entry("legacy");e.packet.sourceVerifiedAtStart=false;
  assert.throws(()=>score([e]),/核验/);
});
