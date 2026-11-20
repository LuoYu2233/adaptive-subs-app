/* Adaptive Subs extension — service worker.
   Does the network work the content script on bilibili.com can't do itself:
     - downloads the subtitle file from Bilibili's file server (hdslb.com)
     - talks to the local Adaptive Subs server (server.py on 127.0.0.1), which keeps the OpenAI key
       and generates the annotations. A web page may not call 127.0.0.1; the extension may. */
"use strict";

const PORTS = Array.from({ length: 20 }, (_, i) => 8765 + i);   // server.py moves up if 8765 is busy
const HEADERS = { "X-Adaptive-Subs": "1" };                     // server.py only accepts this from us
let base = null;
let lastHealth = null, lastCheck = 0;

async function health(b) {
  try {
    const r = await fetch(`${b}/api/health`, { signal: AbortSignal.timeout(1200), cache: "no-store" });
    const j = await r.json();
    return j && j.ok ? j : null;
  } catch {
    return null;
  }
}

async function findServer(force) {
  if (!base) base = (await chrome.storage.session.get("base")).base || null;
  if (base && lastHealth && !force && Date.now() - lastCheck < 30000) return { base, h: lastHealth };
  if (base) {
    const h = await health(base);
    if (h) { lastHealth = h; lastCheck = Date.now(); return { base, h }; }
  }
  for (const p of PORTS) {
    const b = `http://127.0.0.1:${p}`;
    const h = await health(b);
    if (h) {
      base = b; lastHealth = h; lastCheck = Date.now();
      chrome.storage.session.set({ base });
      return { base, h };
    }
  }
  base = null; lastHealth = null;
  return null;
}

async function json(r) {
  try { return await r.json(); } catch { return { error: `本地服务器返回了 HTTP ${r.status}` }; }
}

async function handle(msg) {
  if (msg.type === "subtitle") {
    const u = new URL(msg.url);
    if (u.protocol !== "https:" || !/(^|\.)hdslb\.com$/.test(u.hostname)) throw new Error("字幕地址不是 B 站的文件服务器");
    const r = await fetch(u.href, { cache: "no-store" });
    if (!r.ok) throw new Error(`字幕文件下载失败（HTTP ${r.status}）`);
    return { data: await r.json() };
  }

  const s = await findServer(msg.type === "health");
  if (!s) return { error: "noserver" };
  if (msg.type === "health") {
    return { ok: true, base: s.base, ext: !!s.h.ext, key: !!s.h.key, asr: !!s.h.asr, model: s.h.model,
             provider: s.h.provider_name || "", setup: s.h.setup ? s.base + s.h.setup : "" };
  }
  if (!s.h.ext) return { error: "oldserver" };

  try {
    if (msg.type === "open") {
      const r = await fetch(`${s.base}/api/ext/open`, {
        method: "POST",
        headers: { ...HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify(msg.payload),
      });
      return await json(r);
    }
    if (msg.type === "events") {
      const r = await fetch(`${s.base}/api/ext/events?v=${encodeURIComponent(msg.id)}&since=${msg.since | 0}`,
        { headers: HEADERS, cache: "no-store" });
      return await json(r);
    }
    if (msg.type === "focus") {
      await fetch(`${s.base}/api/focus?v=${encodeURIComponent(msg.id)}&t=${Number(msg.t) || 0}`, { cache: "no-store" });
      return { ok: true };
    }
  } catch {
    lastHealth = null;               // server went away: look for it again next time
    return { error: "noserver" };
  }
  throw new Error("unknown message");
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  handle(msg).then(reply, (e) => reply({ error: String((e && e.message) || e) }));
  return true;   // reply asynchronously
});
