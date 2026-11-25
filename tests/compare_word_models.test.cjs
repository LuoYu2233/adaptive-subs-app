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
  const run = (options, expectedHash) => compare(video,labels,profile,"normal",table,options,{tableSha256:expectedHash});
  const result = run(config,hash);
  assert.equal(result.models.b_initial.theta,0.5); assert.equal(result.models.b_frequency.theta,1.5);
  assert.equal(result.params.abilitySource,"measured theta per table field");
  assert.throws(()=>run(config,"b".repeat(64)),/指纹/);
  assert.throws(()=>run({...config,theta:1},hash),/同时提供/);
  assert.throws(()=>run({...config,thetaByField:{b_initial:1}},hash),/同时提供/);
});

test("cohort weights events and time, pools AUC before scoring, and rejects duplicate learner/video pairs", t => {
  const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
  const { cohort } = require("../tools/compare_word_cohort.cjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),"参与者-"));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const write = (name, data) => fs.writeFileSync(path.join(dir,name),JSON.stringify(data));
  const {video,labels,profile,table} = setup();
  write("长片",video); write("标注",labels); write("人",profile); write("参数",{theta:1}); write("表",table);
  const first = {learner:"P1",domain:"科技",video:"长片",labels:"标注",profile:"人",parameters:"参数"};
  const short = {video:{id:"new",duration:10},cues:[video.cues[0]]};
  const answers = prepare(short); answers.cues[0].unknown = ["easy"];
  write("短片",short); write("短标",answers);
  const second = {...first,learner:"P2",domain:"新闻",video:"短片",labels:"短标"};
  const run = sessions => {
    write("清单",{version:1,sessions});
    return cohort(path.join(dir,"清单"),path.join(dir,"表"),"normal");
  };
  const out = run([first,second]), model = out.overall.models.b_frequency;
  assert.equal(out.participants,2); assert.equal(out.videos,2);
  assert.equal(out.overall.durationSeconds,70);
  assert.equal(model.display.missRate,1/4);
  assert.equal(model.display.extraPerMinute,60/70);
  assert.equal(model.probability.observations,4);
  assert.equal(model.probability.auc,5/6); // a positive ties the single negative across participants
  assert.equal(out.byLearner[1].models.b_frequency.probability.auc,null);
  assert.equal(out.byDomain[0].models.b_frequency.display.missed,0);
  for (const [removed, retained] of [[0,1],[1,0]]) {
    const without = out.pairedDisplay.leaveOneLearnerOut[removed], only = out.byLearner[retained].models;
    assert.equal(without.omitted, out.byLearner[removed].learner);
    assert.equal(without.changes.b_frequency.missRate,only.b_frequency.display.missRate - only.baseline.display.missRate);
    assert.equal(without.changes.b_initial.extraPerMinute,only.b_initial.display.extraPerMinute - only.baseline.display.extraPerMinute);
  }
  assert.equal(out.pairedDisplay.changes.b_initial.f2,out.overall.models.b_initial.display.f2-out.overall.models.baseline.display.f2);
  assert.deepEqual(run([first]).pairedDisplay.leaveOneLearnerOut,[]);
  answers.cues[0].unknown = [];
  write("全熟",answers);
  const negative = run([first,{...second,labels:"全熟"}]);
  assert.equal(negative.pairedDisplay.leaveOneLearnerOut[0].changes.b_initial.missRate,null);
  assert.throws(()=>run([first,first]),/第 2 组.*重复/);
  function windowSession(name, start, end) {
    const subset = prepare(video,{start,end});
    for (const row of subset.cues) row.unknown = labels.cues.find(original => original.text === row.text).unknown;
    write(name,subset);
    return {...first,labels:name};
  }
  const head = windowSession("前段",0,3), tail = windowSession("后段",3,7);
  const paired = run([head,tail]);
  assert.equal(paired.participants,1);
  assert.equal(paired.videos,1);
  assert.equal(paired.overall.durationSeconds,7);
  assert.equal(paired.overall.models.b_frequency.probability.observations,3);
  const media = "a".repeat(64);
  function packetSession(labelFile, name, videoId) {
    const labels = JSON.parse(fs.readFileSync(path.join(dir,labelFile),"utf8"));
    const packet = {version:1,kind:"blind-video-evaluation",mediaSha256:media,video:{...video,video:{...video.video,id:videoId}},
      labels:{...labels,video:videoId},profile};
    write(name,packet);
    return {learner:first.learner,domain:first.domain,parameters:first.parameters,packet:name};
  }
  const readHead = packetSession("前段","前段评分包",video.video.id);
  const readTail = packetSession("后段","后段评分包","另一个字幕轨");
  assert.deepEqual(run([readHead,readTail]),paired);
  assert.throws(()=>run([readHead,{...readTail,packet:readHead.packet}]),/重复/);
  assert.throws(()=>run([readHead,{...readTail,labels:"标注"}]),/请选择/);
  assert.throws(()=>run([readHead,head]),/重复/);
  const overlap = windowSession("重叠",2,6);
  assert.throws(()=>run([tail,overlap]),/第 2 组.*重叠/);
  assert.throws(()=>run([{...first,profile:""}]),/第 1 组.*路径/);
});

test("explicit marks are protected while inferred word knowledge can be reconsidered by the model", () => {
  const {video,labels,profile,table} = setup();
  profile.known.push("hard");
  profile.unknown.push("easy");
  const old = compare(video,labels,profile,"normal",table,{theta:1});
  profile.marks = {known:["manual"],unknown:["forced"]};
  const observed = compare(video,labels,profile,"normal",table,{theta:1});
  assert.deepEqual(old.baseline,observed.baseline);
  assert.equal(old.coverage.profileOverride,4); assert.equal(observed.coverage.profileOverride,2);
  assert.equal(observed.models.b_initial.display.hits,old.models.b_initial.display.hits+1);
  assert.equal(observed.models.b_initial.display.extra,old.models.b_initial.display.extra-1);
  assert.equal(observed.params.overrideSource,"manual marks");
  profile.marks.known = ["absent"];
  assert.throws(()=>compare(video,labels,profile,"normal",table,{theta:1}),/画像不一致/);
});

test("a phrase-only label cannot serve as negative evidence about either constituent word", () => {
  const video = {video:{id:"phrase",duration:10},cues:[{s:0,e:3,text:"take off today",notes:[
    {s:0,e:4,key:"take",kind:"word",lv:9000},{s:5,e:8,key:"off",kind:"word",lv:9000},
    {s:9,e:14,key:"today",kind:"word",lv:9000},
  ]}]};
  const labels = prepare(video); labels.cues[0].unknown = ["take off"];
  const table = {version:1,scale:"natural-log lemma rank",words:Object.fromEntries(
    ["take","off","today"].map(w=>[w,{b_frequency:2,b_initial:2}]))};
  const r = compare(video,labels,{level:2,known:[],unknown:[]},"normal",table,{theta:0});
  assert.equal(r.coverage.classified,3); assert.equal(r.overlappingLabels,2);
  for (const m of Object.values(r.models)) {
    assert.equal(m.probability.observations,1); assert.equal(m.probability.positives,0);
    assert.equal(m.display.missedByReason.noCandidate,1); assert.equal(m.display.extra,3);
  }
});

test("IRT reproduces prefix density but omits unreviewed prefix words from calibration and coverage", () => {
  const {profile,table} = setup();
  const video = {video:{id:"seg",duration:30},cues:[0,10,20].map(s=>({s,e:s+1,text:"hard",
    notes:[{s:0,e:4,key:"hard",kind:"word",lv:1000}]}))};
  const labels = prepare(video,{start:18,end:30}); labels.cues[0].unknown=["hard"];
  const r = compare(video,labels,profile,"normal",table,{theta:1});
  assert.equal(r.coverage.classified,1); assert.equal(r.models.b_initial.probability.observations,1);
  assert.equal(r.models.b_initial.display.missedByReason.displayLimit,1);
  assert.equal(r.baseline.durationSeconds,12);
});
