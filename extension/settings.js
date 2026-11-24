/* Shared validation for popup, initial content load and live storage changes. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AdaptiveSettings = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const LEVELS = Object.freeze([1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000]);
  const DEFAULTS = Object.freeze({ enabled: true, level: 3000, pref: "more", mode: "adaptive",
    gloss: "over", hideNative: true, pos: "bottom", cover: "on" });
  const CHOICES = { pref: ["more", "normal", "less"], mode: ["adaptive", "plain", "bilingual"],
    gloss: ["over", "paren"], pos: ["bottom", "top"], cover: ["on", "off"] };

  function inspect(value) {
    const settings = { ...DEFAULTS };
    if (value === undefined) return { settings, invalid: false };
    if (!value || typeof value !== "object" || Array.isArray(value)) return { settings, invalid: true };
    let invalid = false;
    for (const key of Object.keys(DEFAULTS)) {
      if (!Object.hasOwn(value, key)) continue;
      const v = value[key];
      const valid = key === "level" ? LEVELS.includes(v)
        : key === "enabled" || key === "hideNative" ? typeof v === "boolean" : CHOICES[key].includes(v);
      if (valid) settings[key] = v;
      else invalid = true;
    }
    return { settings, invalid };
  }
  return Object.freeze({ DEFAULTS, LEVELS, inspect, normalize: value => inspect(value).settings });
});
