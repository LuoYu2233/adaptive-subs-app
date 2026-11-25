"use strict";
const el = id => document.getElementById(id);
const data = JSON.parse(el("data").textContent);
let session, result;
let startedAt, questionAt, visibleSince, visibleTotal = 0;

function updateVisibility(now) {
  if (visibleSince !== undefined) visibleTotal += now - visibleSince;
  visibleSince = document.hidden ? undefined : now;
}
document.addEventListener("visibilitychange", () => {
  if (session && !result) updateVisibility(performance.now());
});

function submit(choice) {
  const now = performance.now();
  session.answer(choice);
  session.history[session.history.length - 1].elapsedMs = Math.round(now - questionAt);
  advance();
}
el("prior").max = data.difficulties.b_initial.length - 1;
el("prior").value = Math.min(5000, Number(el("prior").max));

function advance() {
  const question = session.next();
  if (!question) {
    const finished = performance.now();
    updateVisibility(finished);
    result = session.result();
    result.timing = { elapsedMs: Math.round(finished - startedAt), visibleMs: Math.round(visibleTotal) };
    const { expectedVocabulary, theta, se } = result.fits.b_initial;
    el("quiz").hidden = true;
    el("done").hidden = false;
    el("outcome").textContent = `共完成 ${result.responses.length} 题，用时 ${Math.round(result.timing.elapsedMs / 1000)} 秒。按本表估计认识约 ${expectedVocabulary.toLocaleString("zh-CN")} 个词元。`;
    const reason = { precision: "已达到本次试测的精度目标。", limit: "已达到 40 题上限。", bank: "已用完题库。" }[result.stopReason];
    el("precision").textContent = `${reason}能力估计 θ = ${theta.toFixed(2)}，后验标准误为 ${se.toFixed(2)}。题目参数仍待真人校准。`;
    el("complete").focus();
    return;
  }
  el("progress").textContent = `第 ${session.history.length + 1} 题 / 最多 40 题`;
  el("word").textContent = question.item.word;
  el("choices").replaceChildren(...question.choices.map((text, i) => {
    const button = document.createElement("button");
    button.textContent = text;
    button.dataset.key = String(i + 1);
    button.setAttribute("aria-keyshortcuts", button.dataset.key);
    button.onclick = () => submit(i);
    return button;
  }));
  el("word").focus();
  questionAt = performance.now();
}

el("setup").onsubmit = event => {
  event.preventDefault();
  if (!el("prior").reportValidity()) return;
  try { session = new WordCat.Session(data, el("prior").valueAsNumber); }
  catch (error) { el("error").textContent = error.message; return; }
  el("start").hidden = true;
  el("quiz").hidden = false;
  startedAt = performance.now();
  updateVisibility(startedAt);
  advance();
};
el("skip").onclick = () => submit(null);
document.addEventListener("keydown", event => {
  if (el("quiz").hidden || event.repeat || event.isComposing || event.ctrlKey || event.altKey ||
      event.metaKey || event.shiftKey || !/^[0-4]$/.test(event.key)) return;
  event.preventDefault();
  submit(event.key === "0" ? null : Number(event.key) - 1);
});

function save(name, content) {
  const link = document.createElement("a");
  const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2) + "\n"], {type: "application/json"}));
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
el("parameters").onclick = () => save("word-cat-parameters.json", result.parameters);
el("record").onclick = () => save("word-cat-record.json", result);
