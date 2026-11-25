// The controller window owns the answers. The video tab receives only cue indices.
function installLabelPlayback({ getClip, isCurrent, getVideo, isAd, setBlind }) {
  let active = null;

  function detach(session) {
    if (active !== session) return;
    active = null;
    if (isCurrent(session.clip) && !isAd()) getVideo()?.pause();
    setBlind(false);
  }

  function stop(session, reason) {
    session.port.postMessage({ type: "stopped", reason });
    detach(session);
    session.port.disconnect();
  }

  function tick() {
    if (!active) return;
    const session = active;
    if (!isCurrent(session.clip)) return stop(session, "视频已切换。已确认的标注可以导出。");
    const video = getVideo();
    if (!video) return;
    if (isAd()) {
      if (!session.ad) session.port.postMessage({ type: "notice", text: "正在播放广告，结束后可重听本句。" });
      session.ad = true;
      session.end = null;
      return;
    }
    session.ad = false;
    if (session.end === null || video.currentTime >= session.end) {
      if (!video.paused) video.pause();
      session.end = null;
    }
  }

  chrome.runtime.onConnect.addListener(port => {
    if (port.name !== "blind-labels" || port.sender?.id !== chrome.runtime.id ||
        port.sender.url?.split("?")[0] !== chrome.runtime.getURL("label.html")) return;
    const clip = getClip(), video = getVideo();
    if (active || !clip || !video) {
      port.postMessage({ type: "stopped", reason: active ? "此视频已有标注窗口。" : "请先载入已生成的字幕，再打开标注窗口。" });
      port.disconnect();
      return;
    }
    const session = { port, clip, end: null, ad: false, request: 0 };
    active = session;
    setBlind(true);
    if (!isAd()) video.pause();
    port.onDisconnect.addListener(() => detach(session));
    port.onMessage.addListener(async msg => {
      if (active !== session || !isCurrent(clip)) return;
      const media = getVideo();
      if (!media || isAd()) {
        port.postMessage({ type: "notice", text: "当前视频还不能定位，请在广告结束或播放器就绪后重听。" });
        return;
      }
      const cue = clip.sheet.cues[msg.index];
      if (msg.type !== "cue" || !Number.isInteger(msg.index) || !cue) return;
      const request = ++session.request;
      try {
        media.pause();
        session.end = msg.play ? cue.e : null;
        media.currentTime = cue.s;
        if (msg.play) await media.play();
      } catch {
        if (active === session && request === session.request)
          port.postMessage({ type: "notice", text: "播放器暂不能自动播放，请在原视频页点播放，或稍后重听本句。" });
      }
    });
    port.postMessage({ type: "sheet", sheet: clip.sheet, title: clip.title, candidates: clip.candidates });
  });

  return { tick, get active() { return !!active; } };
}
