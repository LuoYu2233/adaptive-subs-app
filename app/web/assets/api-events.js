/* Local job events use authenticated fetches; native EventSource cannot set headers. */
(function (root) {
  "use strict";
  class AdaptiveEvents {
    constructor(id, fetcher = (...args) => fetch(...args)) {
      this.id = id;
      this.fetcher = fetcher;
      this.listeners = new Map();
      this.readyState = 0;
      this.since = 0;
      this.controller = new AbortController();
      this.finished = this.run();
    }
    addEventListener(type, callback) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(callback);
    }
    close() {
      this.readyState = 2;
      this.controller.abort();
    }
    async run() {
      let errors = 0;
      while (this.readyState !== 2) {
        try {
          const response = await this.fetcher(`api/ext/events?v=${encodeURIComponent(this.id)}&since=${this.since}`, {
            headers: { "X-Adaptive-Subs": "1" }, cache: "no-store", signal: this.controller.signal,
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          if (!Array.isArray(data.events) || !Number.isSafeInteger(data.next)) throw new Error("Invalid events");
          if (this.readyState === 2) return;
          errors = 0;
          this.readyState = 1;
          this.since = data.next;
          for (const [type, payload] of data.events) {
            for (const fn of this.listeners.get(type) || []) fn({ data: JSON.stringify(payload) });
            if (type === "done" || type === "fail") { this.close(); return; }
            if (this.readyState === 2) return;
          }
        } catch (err) {
          if (this.readyState === 2) return;
          if (++errors >= 5) {
            this.close();
            if (this.onerror) this.onerror(err);
            return;
          }
          await new Promise(resolve => setTimeout(resolve, Math.min(errors * 1000, 5000)));
        }
      }
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = AdaptiveEvents;
  else root.AdaptiveEvents = AdaptiveEvents;
})(typeof globalThis !== "undefined" ? globalThis : this);
