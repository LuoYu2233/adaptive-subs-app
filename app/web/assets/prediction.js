// Canonical source. The unpacked extension ships an identical copy (checked by prediction.test.cjs).
(function () {
  "use strict";

  const preferences = {
    more: { factor: 0.7, perLine: 4, repeat: 3 },
    normal: { factor: 1.0, perLine: 3, repeat: 2 },
    less: { factor: 1.3, perLine: 2, repeat: 1 },
  };

  function markKey(note) {
    return note.kind === "word" ? note.key : `${note.kind}:${note.key}`;
  }

  function classify(note, known, unknown, threshold) {
    const key = markKey(note);
    if (unknown.has(key)) return "forced";
    if (known.has(key)) return "known";
    return note.lv > threshold ? "hard" : "easy";
  }

  // counts belongs to one video. Only displayed annotations consume its repeat allowance.
  function selectNotes(notes, classifyNote, preference, counts) {
    const candidates = [];
    notes.forEach((note, index) => {
      const state = classifyNote(note);
      if (state === "hard" || state === "forced") {
        candidates.push({ index, note, forced: state === "forced" });
      }
    });
    candidates.sort((a, b) => Number(b.forced) - Number(a.forced) || b.note.lv - a.note.lv);

    const selected = new Set();
    for (const candidate of candidates) {
      if (selected.size >= preference.perLine) break;
      const key = markKey(candidate.note);
      const count = counts.get(key) || 0;
      if (!candidate.forced && count >= preference.repeat) continue;
      selected.add(candidate.index);
      counts.set(key, count + 1);
    }
    return selected;
  }

  const prediction = { preferences, markKey, classify, selectNotes };
  if (typeof module === "object" && module.exports) module.exports = prediction;
  else globalThis.AdaptivePrediction = prediction;
})();
