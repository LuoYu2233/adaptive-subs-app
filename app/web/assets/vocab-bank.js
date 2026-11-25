/* Validate the complete question bank before any answers can affect a learner profile. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AdaptiveVocabBank = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const MAX_BYTES = 2 * 1024 * 1024;
  const word = /^[a-z][a-z'-]{0,39}$/;
  const object = v => v && typeof v === "object" && !Array.isArray(v);
  const text = (v, max) => typeof v === "string" && v.trim().length > 0 && v.length <= max
    && ![...v].some(c => c.codePointAt(0) >= 0xD800 && c.codePointAt(0) <= 0xDFFF);
  function invalid() { throw new Error("题库格式无效，请重新加载；若仍失败，请修复或重新安装程序。"); }
  function validate(value) {
    if (!object(value) || !Array.isArray(value.bands) || value.bands.length < 2 || value.bands.length > 20
        || !Array.isArray(value.nonwords) || value.nonwords.length < 6 || value.nonwords.length > 200) invalid();
    const seen = new Set();
    let end = 0, total = 0;
    const bands = value.bands.map(b => {
      if (!object(b) || !Number.isSafeInteger(b.lo) || !Number.isSafeInteger(b.hi)
          || b.lo !== end + 1 || b.hi < b.lo || b.hi > 100000
          || !Array.isArray(b.words) || b.words.length < 11 || b.words.length > 200) invalid();
      end = b.hi;
      total += b.words.length;
      if (total > 2000) invalid();
      const words = b.words.map(item => {
        if (!object(item) || typeof item.word !== "string" || !word.test(item.word) || seen.has(item.word)
            || !text(item.zh, 80) || !text(item.example, 400) || !Array.isArray(item.distractors)
            || item.distractors.length !== 3 || !item.distractors.every(v => text(v, 80))) invalid();
        const options = [item.zh, ...item.distractors].map(v => v.trim());
        if (new Set(options).size !== 4 || options.includes("不确定")) invalid();
        seen.add(item.word);
        return { word: item.word, zh: options[0], example: item.example.trim(), distractors: options.slice(1) };
      });
      return { lo: b.lo, hi: b.hi, words };
    });
    const nonwords = value.nonwords.map(w => {
      if (typeof w !== "string" || !word.test(w) || seen.has(w)) invalid();
      seen.add(w);
      return w;
    });
    return { bands, nonwords };
  }
  async function read(url) {
    const response = await fetch(url, { signal: AbortSignal.timeout(10000), cache: "no-store" });
    if (!response.ok) throw new Error(`题库读取失败（HTTP ${response.status}）`);
    const tooLarge = () => new Error("题库文件过大，请检查程序文件");
    if (Number(response.headers.get("Content-Length")) > MAX_BYTES) {
      await response.body?.cancel();
      throw tooLarge();
    }
    if (!response.body) invalid();
    const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true });
    let size = 0, raw = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BYTES) throw tooLarge();
        raw += decoder.decode(value, { stream: true });
      }
      return validate(JSON.parse(raw + decoder.decode()));
    } catch (error) {
      await reader.cancel().catch(() => {});
      throw error;
    } finally { reader.releaseLock(); }
  }

  function loader(url) {
    let pending;
    return () => {
      if (!pending) pending = read(url).catch(error => { pending = null; throw error; });
      return pending;
    };
  }
  return Object.freeze({ validate, loader, MAX_BYTES });
});
