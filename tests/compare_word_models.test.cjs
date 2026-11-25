const test = require("node:test");
const assert = require("node:assert/strict");
const { compare, abilityForLevel, probabilityMetrics } = require("../tools/compare_word_models.cjs");
const { prepare } = require("../tools/evaluate_prediction.cjs");

function setup() {
  const terms = [
    ["easy", "word", 6000], ["hard", "word", 1000], ["equal", "word", 1000],
    ["missing", "word", 9000], ["take off", "phrase", 1000], ["manual", "word", 1000], ["forced", "word", 1000],
  ];
  const video = { video: {id:"fixture", duration:60}, cues:terms.map(([term, kind, lv], i) => ({
    s:i, e:i+1, text:term, notes:[{s:0, e:term.length, key:term, kind, lv}],
  })) };
  const labels = prepare(video);
  labels.cues.forEach(row => { row.unknown = ["hard", "equal", "forced"].includes(row.text) ? [row.text] : []; });
  const profile = {level:5000, known:["manual"], unknown:["forced"]};
  const table = {version:1, scale:"natural-log lemma rank", words: {
    easy:{b_frequency:-1,b_initial:-0.8}, hard:{b_frequency:3,b_initial:2.8}, equal:{b_frequency:1,b_initial:1},
    manual:{b_frequency:10,b_initial:10}, forced:{b_frequency:-10,b_initial:-10},
  }};
  return { video, labels, profile, table };
}

test("shadow scoring changes only eligible words, includes the exact threshold and keeps display constraints", () => {
  const {video, labels, profile, table} = setup();
  const r = compare(video, labels, profile, "normal", table, {theta:1});
  assert.deepEqual(r.coverage,{classified:3, profileOverride:2, missingWord:1, otherKind:1});
  assert.deepEqual([r.baseline.hits,r.baseline.extra,r.baseline.missed], [1,2,2]);
  const f = r.models.b_frequency;
  assert.deepEqual([f.display.hits,f.display.extra,f.display.missed], [3,1,0]);
  assert.equal(f.probability.auc,1);
  assert.equal(f.probability.observations,3);
  assert.equal(f.probability.positives,2);
  const sparse = compare(video, labels, profile, "less", table, {theta:1});
  assert.equal(sparse.models.b_frequency.display.missed,1);
  const adjusted = compare(video, labels, profile, "less", table, {theta:1, viewingDiscount:1});
  assert.equal(adjusted.models.b_frequency.display.missed,0);
  // Repeat the hard occurrence three times; normal density allows two.
  video.cues = [0,1,2].map(s=>({...video.cues[1],s,e:s+1}));
  const repeated = prepare(video);
  repeated.cues.forEach(row => row.unknown = ["hard"]);
  assert.equal(compare(video,repeated,profile,"normal",table,{theta:1}).models.b_frequency.display.missedByReason.displayLimit,1);
});

test("expected vocabulary inversion uses the whole table and does not depend on the labels", () => {
  const bs = [-3, -2, -1, 0, 1, 2, 3];
  assert.ok(Math.abs(abilityForLevel(bs,3.5,2)) < 1e-12);
  const theta = abilityForLevel(bs,5,1.7);
  const known = bs.reduce((sum,b)=>sum+1/(1+Math.exp(1.7*(b-theta))),0);
  assert.ok(Math.abs(known - 5) < 1e-10);
  const {video, labels, profile, table} = setup();
  profile.level = 3;
  const before = compare(video,labels,profile,"normal",table);
  labels.cues.forEach(row => row.unknown = []);
  const after = compare(video,labels,profile,"normal",table);
  assert.equal(before.models.b_initial.theta,after.models.b_initial.theta);
  assert.notEqual(before.models.b_initial.theta,before.models.b_frequency.theta);
});

test("log loss remains finite at extreme scores and AUC deals with ties or a missing class", () => {
  const one = probabilityMetrics([{logit:1000,unknown:false}]);
  assert.equal(one.logLoss,1000); assert.equal(one.auc,null);
  const ties = probabilityMetrics([{logit:0,unknown:true},{logit:0,unknown:false}]);
  assert.equal(ties.auc,0.5); assert.equal(ties.logLoss,Math.log(2));
  assert.deepEqual(ties.calibration,[{lower:0.5,upper:0.6,count:2,meanPredicted:0.5,observedUnknown:0.5}]);
  assert.deepEqual(probabilityMetrics([]),{observations:0,positives:0,logLoss:null,auc:null,calibration:[]});
});

test("invalid model settings, table values and unreviewed clips fail before reporting metrics", () => {
  const {video, labels, profile, table} = setup();
  const score = cfg => compare(video,labels,profile,"normal",table,cfg);
  assert.throws(()=>score({}),/词汇量/);
  for (const config of [{theta:Infinity},{discrimination:0},{viewingDiscount:-1},{guess:0.25}]) assert.throws(()=>score(config));
  labels.cues[0].unknown = null;
  assert.throws(()=>score({theta:1}),/尚未标注/);
  labels.cues[0].unknown = [];
  table.words.easy.b_initial = NaN;
  assert.throws(()=>score({theta:1}),/难度/);
});

test("measured abilities are field-specific and can only be used with the table they came from", () => {
  const {video,labels,profile,table} = setup();
  const hash = "a".repeat(64);
  const config = {tableSha256:hash,thetaByField:{b_initial:0.5,b_frequency:1.5},discrimination:2};
  const run = (options, expectedHash) => compare(video,labels,profile,"normal",table,options,expectedHash);
  const result = run(config,hash);
  assert.equal(result.models.b_initial.theta,0.5); assert.equal(result.models.b_frequency.theta,1.5);
  assert.equal(result.params.abilitySource,"measured theta per table field");
  assert.throws(()=>run(config,"b".repeat(64)),/指纹/);
  assert.throws(()=>run({...config,theta:1},hash),/同时提供/);
  assert.throws(()=>run({...config,thetaByField:{b_initial:1}},hash),/同时提供/);
});
