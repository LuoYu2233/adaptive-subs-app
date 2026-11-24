/* Shared profile writes: preserve intent order and reconcile optimistic UI with durable state. */
(function (root) {
  "use strict";
  const valid = p => p && Number.isInteger(p.rev) && Array.isArray(p.known) && Array.isArray(p.unknown)
    && p.marks && Array.isArray(p.marks.known) && Array.isArray(p.marks.unknown);
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
          const value = await write(entry.op);
          // Older servers return only a revision after marking.
          accept(valid(value) ? value : await read());
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
