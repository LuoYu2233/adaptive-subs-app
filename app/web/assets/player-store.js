/* Local player storage: validate before use, serialize cooperating tabs, preserve corrupt data. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./wordbook.js"));
  else root.AdaptivePlayerStore = factory(root.AdaptiveWordbook);
})(globalThis, function (Wordbook) {
  "use strict";
  const KEY = "adaptive-subs:v2", RECOVERY_PREFIX = KEY + ":recovery:";
  class StorageProblem extends Error {
    constructor(message, code, raw) { super(message); this.code = code; this.raw = raw; }
  }
  function validate(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("不是有效的播放器记录");
    if (value.storageVersion !== undefined && value.storageVersion !== 1) throw new Error("播放器数据版本不兼容");
    for (const [field, allowed] of Object.entries({ mode: ["adaptive", "plain", "bilingual"], gloss: ["over", "paren"],
      pref: ["more", "normal", "less"], subPos: ["inside", "below"], tab: ["transcript", "preview", "book"] })) {
      if (value[field] !== undefined && !allowed.includes(value[field])) throw new Error(`${field} 设置无效`);
    }
    if (value.level !== undefined && (!Number.isFinite(value.level) || value.level <= 0)) throw new Error("词汇量无效");
    for (const field of ["autoPause", "introSeen"])
      if (value[field] !== undefined && typeof value[field] !== "boolean") throw new Error(`${field} 设置无效`);
    for (const field of ["known", "unknown"])
      if (value[field] !== undefined && (!Array.isArray(value[field]) || value[field].length > 100000
          || value[field].some(key => typeof key !== "string" || !key || key.length > 500 || /[\uD800-\uDFFF]/u.test(key))))
        throw new Error("认识标记格式无效");
    if (value.book !== undefined) Wordbook.entries(value.book);
    if (value.test != null && (typeof value.test !== "object" || Array.isArray(value.test)
        || !Number.isFinite(value.test.est) || value.test.est < 0 || typeof value.test.date !== "string"
        || !/^\d{4}-\d{2}-\d{2}$/.test(value.test.date))) throw new Error("词汇测试记录无效");
    return value;
  }
  function decode(raw) {
    if (raw === null) return {};
    try { return validate(JSON.parse(raw)); }
    catch (e) { throw new StorageProblem(`播放器本地数据损坏或不兼容：${e.message}`, "damaged", raw); }
  }
  function create({ storage = () => localStorage, locks = () => navigator.locks, timeout = 5000 } = {}) {
    function raw() {
      try { return storage().getItem(KEY); }
      catch { throw new StorageProblem("浏览器存储不可用，请检查浏览器权限和存储空间", "unavailable"); }
    }
    function read() { return decode(raw()); }
    async function locked(action) {
      const manager = locks();
      if (!manager?.request) throw new StorageProblem("此浏览器无法协调多个页面的保存，请更新浏览器并通过本地程序打开播放器", "unsupported");
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
      try { return await manager.request(KEY, { signal: controller.signal }, action); }
      catch (e) {
        if (e.name === "AbortError") throw new StorageProblem("另一个页面正在保存，请稍后重试", "busy");
        throw e;
      } finally { clearTimeout(timer); }
    }
    async function update(edit) {
      return locked(() => {
        const previous = raw(), current = decode(previous);
        const next = edit(current);
        if (next && typeof next.then === "function") throw new Error("Storage edits must be synchronous");
        validate(next);
        // Old clients do not use this lock. Detect an intervening write when possible.
        if (raw() !== previous) throw new StorageProblem("本地记录已变化，请重试", "conflict");
        storage().setItem(KEY, JSON.stringify({ ...next, storageVersion: 1 }));
        return next;
      });
    }
    async function recover(expected) {
      return locked(() => {
        const original = raw();
        if (original !== expected) throw new StorageProblem("本地记录已变化，请刷新后重新检查", "conflict");
        try { decode(original); }
        catch (e) {
          if (e.code !== "damaged") throw e;
          const key = RECOVERY_PREFIX + Date.now() + ":" + Math.random().toString(36).slice(2);
          if (storage().getItem(key) !== null) throw new Error("恢复副本名称冲突，请重试");
          storage().setItem(key, original);
          if (storage().getItem(key) !== original) throw new Error("恢复副本校验失败，未重建");
          if (raw() !== original) throw new StorageProblem("本地记录已变化，未重建", "conflict");
          storage().setItem(KEY, JSON.stringify({ storageVersion: 1 }));
          return key;
        }
        throw new Error("当前数据正常，无需重建");
      });
    }
    function recoveryCopies() {
      const target = storage(), copies = [];
      for (let i = 0; i < target.length; i++) {
        const key = target.key(i);
        if (key?.startsWith(RECOVERY_PREFIX)) copies.push({ key, raw: target.getItem(key) });
      }
      return copies;
    }
    return { read, update, recover, recoveryCopies };
  }
  return { KEY, RECOVERY_PREFIX, StorageProblem, validate, decode, create };
});
