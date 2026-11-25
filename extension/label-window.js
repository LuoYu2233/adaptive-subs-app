// A range refers to the original video timeline. Score only cues heard in their entirety.
"use strict";
const LabelWindow = (() => {
  function seconds(input) {
    const value = String(input).trim();
    if (/^\d+(?:\.\d+)?$/.test(value)) return Number(value);
    const match = /^(\d+):([0-5]\d(?:\.\d+)?)$/.exec(value);
    if (!match) throw new Error("时间请填写秒数或 分:秒，例如 90 或 1:30");
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function indices(cues, duration, range) {
    if (range === undefined) return cues.map((_, i) => i);
    if (!Number.isFinite(range?.start) || !Number.isFinite(range?.end) || range.start < 0 ||
        range.start >= range.end || range.end > duration)
      throw new Error("片段起点须早于终点，且须在视频时长内");
    const selected = [];
    cues.forEach((cue, i) => {
      if (cue.s >= range.start && cue.e <= range.end) selected.push(i);
    });
    if (!selected.length) throw new Error("此片段没有完整字幕句，请调整起止时间");
    return selected;
  }

  return { seconds, indices };
})();
if (typeof module !== "undefined") module.exports = LabelWindow;
