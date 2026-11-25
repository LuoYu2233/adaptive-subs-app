/* Canonical learning rules. Run tools/sync_assets.py after editing this file. */
(function (root) {
  "use strict";
  const cached = new WeakMap();
  const legacyKey = n => n.kind === "word" ? n.key : `${n.kind}:${n.key}`;
  function key(n) {
    if (!n.zh || n.local) return legacyKey(n);
    const source = `${String(n.key).trim().toLowerCase()}\0${String(n.zh).normalize("NFKC").replace(/[\s，。；！？，、:：,.!?;（）()]/g, "")}`;
    const prev = cached.get(n);
    if (prev && prev.source === source) return prev.id;
    // FNV-1a is an identity checksum, not an authentication or security token.
    let hash = 14695981039346656037n;
    for (const byte of new TextEncoder().encode(source)) {
      hash = BigInt.asUintN(64, (hash ^ BigInt(byte)) * 1099511628211n);
    }
    const term = String(n.key).toLowerCase().replace(/[^a-z' .-]/g, "").replace(/^[^a-z]+/, "").trim().slice(0, 60) || "term";
    const id = `ctx:${term}:${hash.toString(16).padStart(16, "0")}`;
    cached.set(n, { source, id });
    return id;
  }
  function state(n, known, unknown, threshold) {
    const specific = key(n), legacy = legacyKey(n);
    if (unknown.has(specific)) return "forced";
    if (known.has(specific)) return "known";
    if (unknown.has(legacy)) return "forced";
    if (known.has(legacy)) return "known";
    return n.lv > threshold ? "hard" : "easy";
  }
  function select(notes, known, unknown, threshold, preference, counts) {
    const candidates = [];
    notes.forEach((n, i) => {
      const status = state(n, known, unknown, threshold);
      if (status === "forced" || status === "hard") candidates.push({ i, id: key(n), level: n.lv, forced: status === "forced" });
    });
    candidates.sort((a, b) => Number(b.forced) - Number(a.forced) || b.level - a.level);
    const selected = new Set();
    for (const candidate of candidates) {
      const seen = counts.get(candidate.id) || 0;
      if (!candidate.forced && (selected.size >= preference.perLine || seen >= preference.repeat)) continue;
      selected.add(candidate.i);
      counts.set(candidate.id, seen + 1);
    }
    return selected;
  }
  const label = k => k.startsWith("ctx:") ? k.slice(4, k.lastIndexOf(":")) : k.replace(/^(sense|phrase):/, "");
  function clientNotes(text, notes = []) {
    // Persisted/server offsets are Python Unicode code points; JS slice() uses UTF-16 units.
    const offsets = [0];
    for (const point of text) offsets.push(offsets[offsets.length - 1] + point.length);
    return notes.map(note => {
      const limit = note.offsets === "utf16" ? text.length : offsets.length - 1;
      if (!Number.isInteger(note.s) || !Number.isInteger(note.e) || note.s < 0 || note.e <= note.s || note.e > limit)
        throw new Error("字幕注释位置无效，请重新生成");
      return { ...note, s: note.offsets === "utf16" ? note.s : offsets[note.s],
        e: note.offsets === "utf16" ? note.e : offsets[note.e], offsets: "utf16" };
    });
  }
  const api = { key, legacyKey, state, select, label, clientNotes };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AdaptiveLearning = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
