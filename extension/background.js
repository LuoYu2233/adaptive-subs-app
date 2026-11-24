/* Adaptive Subs extension — service worker.
   Does the network work the content script on bilibili.com / youtube.com can't do itself:
     - downloads Bilibili subtitle files from Bilibili's file server (hdslb.com)
     - talks to the local Adaptive Subs server (server.py on 127.0.0.1), which keeps the OpenAI key
       and generates the annotations. A web page may not call 127.0.0.1; the extension may. */
"use strict";

const PORTS = Array.from({ length: 20 }, (_, i) => 8765 + i);   // server.py moves up if 8765 is busy
const HEADERS = { "X-Adaptive-Subs": "1" };                     // server.py only accepts this from us
let base = null;
let lastHealth = null, lastCheck = 0;
let profileWrites = Promise.resolve();

async function health(b) {
  try {
    // Session storage is a hint; discovery only accepts loopback addresses and product metadata.
    const address = new URL(b);
    if (address.protocol !== "http:" || address.hostname !== "127.0.0.1" || address.username || address.password
        || address.pathname !== "/" || address.search || address.hash) return null;
    const r = await fetch(`${address.origin}/api/health`, { signal: AbortSignal.timeout(1200), cache: "no-store", redirect: "error", credentials: "omit" });
    if (!r.ok) return null;
    const raw = await r.text();
    if (raw.length > 16384) return null;
    const j = JSON.parse(raw);
    const valid = j && j.ok === true && (j.app === undefined || j.app === "adaptive-subs")
      && typeof j.version === "string" && j.version.length <= 80 && /^[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][0-9A-Za-z.-]+)?$/.test(j.version)
      && Number.isInteger(j.ext) && j.ext >= 1 && j.setup === "/setup" && j.profile === "/profile";
    return valid ? j : null;
  } catch {
    return null;
  }
}

async function findServer(force) {
  if (!base) base = (await chrome.storage.session.get("base")).base || null;
  if (base && lastHealth && !force && Date.now() - lastCheck < 30000) return { base, h: lastHealth };
  if (base) {
    const h = await health(base);
    if (h) { base = new URL(base).origin; lastHealth = h; lastCheck = Date.now(); return { base, h }; }
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

async function perform(msg) {
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
             version: s.h.version, versionMismatch: s.h.version !== chrome.runtime.getManifest().version_name,
             runtime: s.h.runtime,
             provider: s.h.provider_name || "", setup: s.h.setup ? s.base + s.h.setup : "",
             profile: s.h.profile ? s.base + s.h.profile : "" };
  }
  if (!s.h.ext) return { error: "oldserver" };
  // YouTube videos need a program that fetches YouTube subtitles for the extension (v0.3+, ext >= 2)
  if (msg.type === "open" && msg.payload && (msg.payload.source === "youtube" || msg.payload.yt) && (s.h.ext | 0) < 2) {
    return { error: "oldserver" };
  }

  try {
    // learner profile + known-word library (shared with the player and the profile page)
    if (msg.type === "profile") {
      if (!s.h.profile) return { error: "oldserver" };
      const r = await fetch(`${s.base}/api/profile${msg.summary ? "?summary=1" : ""}`, { headers: HEADERS, cache: "no-store" });
      return await json(r);
    }
    if (msg.type === "mark" || msg.type === "setprofile" || msg.type === "explain") {
      const send = async () => {
        const r = await fetch(`${s.base}/api/${{ mark: "mark", setprofile: "profile", explain: "explain" }[msg.type]}`, {
        method: "POST",
        headers: { ...HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify(msg.payload),
        });
        return await json(r);
      };
      return await send();
    }
    if (msg.type === "define") {
      const r = await fetch(`${s.base}/api/define?w=${encodeURIComponent(msg.word)}`, { headers: HEADERS, cache: "no-store" });
      return await json(r);
    }
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
      await fetch(`${s.base}/api/focus`, { method: "POST", headers: { ...HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify({ v: msg.id, t: Number(msg.t) || 0 }), cache: "no-store" });
      return { ok: true };
    }
  } catch {
    lastHealth = null;               // server went away: look for it again next time
    return { error: "noserver" };
  }
  throw new Error("unknown message");
}

function handle(msg) {
  if (msg.type !== "mark" && msg.type !== "setprofile") return perform(msg);
  const result = profileWrites.then(() => perform(msg));
  profileWrites = result.catch(() => {});
  return result;
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  handle(msg).then(reply, (e) => reply({ error: String((e && e.message) || e) }));
  return true;   // reply asynchronously
});
