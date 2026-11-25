const {test} = require("node:test");
const assert = require("node:assert/strict");
const settings = require("../extension/settings.js");

test("new and partial settings retain defaults without rewriting their input", () => {
  assert.deepEqual(settings.inspect(undefined), {settings: {...settings.DEFAULTS}, invalid: false});
  const original = {enabled: false, level: 5000, pref: "less", future: "retained in original storage"};
  const before = JSON.stringify(original);
  assert.deepEqual(settings.inspect(original), {settings: {...settings.DEFAULTS, enabled: false, level: 5000, pref: "less"}, invalid: false});
  assert.equal(JSON.stringify(original), before);
});

test("invalid values fall back independently while valid preferences survive", () => {
  const input = {enabled: "false", level: null, mode: "unknown", pref: "less", hideNative: false, pos: "top", cover: "off"};
  const result = settings.inspect(input);
  assert.equal(result.invalid, true);
  assert.deepEqual(result.settings, {...settings.DEFAULTS, pref: "less", hideNative: false, pos: "top", cover: "off"});
  assert.equal(input.level, null);
});

test("malformed stored roots cannot break the popup or content script", () => {
  for (const value of [null, [], 42, false, "broken"]) {
    assert.deepEqual(settings.inspect(value), {settings: {...settings.DEFAULTS}, invalid: true});
  }
  for (const value of [null, "3000", NaN, Infinity, -1, 0, 3000.5, 999999, {}, []]) {
    assert.equal(settings.normalize({level: value}).level, 3000);
  }
});

test("prototype and unrelated fields are not accepted as settings", () => {
  const inherited = Object.create({enabled: false, level: 1500});
  inherited.mode = "bilingual";
  assert.deepEqual(settings.normalize(inherited), {...settings.DEFAULTS, mode: "bilingual"});
  const polluted = JSON.parse('{"__proto__":{"level":1500},"constructor":"bad","pref":"normal"}');
  assert.deepEqual(settings.normalize(polluted), {...settings.DEFAULTS, pref: "normal"});
  assert.equal({}.level, undefined);
});
