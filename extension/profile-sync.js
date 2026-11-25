/* Shared profile writes: preserve intent order and reconcile optimistic UI with durable state. */
(function (root) {
  "use strict";
  const revision = n => Number.isSafeInteger(n) && n >= 0;
  const keys = list => Array.isArray(list) && list.every(key => typeof key === "string" && key.length > 0);
  const valid = p => p && revision(p.rev) && Number.isSafeInteger(p.level) && p.level > 0 && p.level <= 100000
    && keys(p.known) && keys(p.unknown) && p.marks && keys(p.marks.known) && keys(p.marks.unknown);

  function matches(profile, op) {
    if (op.type === "level") return profile.level === op.level;
    return profile.marks.known.includes(op.key) === (op.state === "known")
      && profile.marks.unknown.includes(op.key) === (op.state === "unknown");
  }
  function create({ read, write, apply }) {
    let confirmed = null, tail = Promise.resolve();
    const pending = [];
    function publish() {
      if (!confirmed) return;
      const known = new Set(confirmed.known), unknown = new Set(confirmed.unknown);
      const mk = new Set(confirmed.marks.known), mu = new Set(confirmed.marks.unknown);
      let level = confirmed.level;
      for (const { op } of pending) {
        if (op.type === "level") { level = op.level; continue; }
        known.delete(op.key); unknown.delete(op.key); mk.delete(op.key); mu.delete(op.key);
        if (op.state === "known") { known.add(op.key); mk.add(op.key); }
        if (op.state === "unknown") { unknown.add(op.key); mu.add(op.key); }
      }
      apply({ ...confirmed, level, known: [...known], unknown: [...unknown], marks: { known: [...mk], unknown: [...mu] } });
    }
    function accept(p) {
      if (!valid(p)) throw new Error("画像响应不完整，请检查本地程序");
      if (!confirmed || p.rev >= confirmed.rev) confirmed = p;
    }
    async function refresh() {
      accept(await read()); publish();
    }
    function mutate(op) {
      if (!confirmed) return Promise.reject(new Error("画像尚未加载，请确认本地程序正在运行后重试"));
      const entry = { op: { ...op } };
      pending.push(entry); publish();
      const result = tail.then(async () => {
        try {
          const before = confirmed.rev, value = await write(entry.op);
          // Older servers acknowledge word marks with just {ok, rev}.
          const receipt = entry.op.type === "mark" && value?.ok === true && revision(value.rev)
            && Object.keys(value).every(key => key === "ok" || key === "rev");
          const snapshot = receipt ? await read() : value;
          if (!valid(snapshot) || snapshot.rev <= before || (receipt && snapshot.rev < value.rev)
              || !matches(snapshot, entry.op)) throw new Error("本机画像未确认本次更改，请重新核对");
          if (snapshot.rev >= confirmed.rev) confirmed = snapshot;
          if (!matches(confirmed, entry.op)) throw new Error("画像已在其他页面更新，请重新核对");
        } catch (error) {
          // The server may have saved a write whose response was lost. Reconcile without replaying it.
          try { accept(await read()); } catch { /* keep last confirmed state */ }
          throw error;
        } finally {
          pending.splice(pending.indexOf(entry), 1); publish();
        }
      });
      tail = result.catch(() => {});
      return result;
    }
    return { refresh, mutate };
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { create };
  else root.AdaptiveProfileSync = { create };
})(typeof globalThis !== "undefined" ? globalThis : this);
