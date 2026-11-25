"use strict";
const AdaptiveWordIRT = (() => {
  const thresholds = { more: 0.3, normal: 0.5, less: 0.7 };

  function sigmoid(logit) {
    const small = Math.exp(-Math.abs(logit));
    return logit >= 0 ? 1 / (1 + small) : small / (1 + small);
  }


  function unknownLogit(b, theta, a, domainBias, viewingDiscount) {
    const logit = a * (b - (theta + domainBias - viewingDiscount));
    if (!Number.isFinite(logit)) throw new Error("参数超出可计算的范围");
    return logit;
  }

  function checkedRows(table) {
    if (table?.version !== 1 || table.scale !== "natural-log lemma rank" || !table.words || Array.isArray(table.words) ||
        typeof table.words !== "object" || !Object.keys(table.words).length)
      throw new Error("需要 P1 生成的 v1 词难度表");
    const rows = Object.values(table.words);
    if (rows.some(r => !Number.isFinite(r?.b_frequency) || !Number.isFinite(r?.b_initial)))
      throw new Error("词难度表包含无效的难度");
    return rows;
  }

  function parameters(config, tableSha256) {
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
    return { a, viewingDiscount, domainBias, calibrated };
  }

  // Compile once at import time. Tabs get only the per-word probabilities for the selected scale.
  function compile(table, config, fingerprint, field) {
    if (!["b_initial", "b_frequency"].includes(field)) throw new Error("未知的词难度量尺");
    checkedRows(table);
    const { a, domainBias, viewingDiscount, calibrated } = parameters(config, fingerprint);
    if (!calibrated) throw new Error("请使用单词试测导出的判词参数文件");
    const theta = config.thetaByField[field];
    const probabilities = Object.fromEntries(Object.entries(table.words).map(([word, row]) =>
      [word, sigmoid(unknownLogit(row[field], theta, a, domainBias, viewingDiscount))]));
    return { version: 1, fingerprint, field, probabilities };
  }

  function validPreview(model) {
    if (model?.version !== 1 || typeof model.fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(model.fingerprint) ||
        !["b_initial", "b_frequency"].includes(model.field) || !model.probabilities ||
        typeof model.probabilities !== "object" || Array.isArray(model.probabilities)) return false;
    const values = Object.values(model.probabilities);
    return values.length > 0 && values.every(p => Number.isFinite(p) && p >= 0 && p <= 1);
  }

  return { thresholds, sigmoid, checkedRows, parameters, unknownLogit, compile, validPreview };
})();
if (typeof module !== "undefined") module.exports = AdaptiveWordIRT;
