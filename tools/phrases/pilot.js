"use strict";
const el = id => document.getElementById(id);
const bank = JSON.parse(el("data").textContent);
let session, result, questionAt;
function advance() {
  const q = session.next();
  if (!q) {
    result = session.result();
    el("quiz").hidden = true; el("done").hidden = false;
    const n = result.responses.length, hits = result.responses.filter(r => r.correct).length;
    el("score").textContent = `完成 ${n} 道语境题，答对 ${hits} 道。`;
    el("uncertainty").textContent = `短语能力估计 θ = ${result.thetaPhrase.toFixed(2)}，后验标准误 ${result.se.toFixed(2)}。`;
    el("complete").focus();
    return;
  }
  el("progress").textContent = `第 ${session.history.length + 1} 题 / 最多 8 题`;
  const marked = document.createElement("mark");
  marked.textContent = q.item.target;
  el("sentence").replaceChildren(q.item.before, marked, q.item.after);
  el("choices").replaceChildren(...q.choices.map((choice, index) => {
    const b = document.createElement("button");
    b.textContent = `${index + 1} · ${choice}`; b.setAttribute("aria-keyshortcuts", String(index + 1));
    b.onclick = () => submit(index);
    return b;
  }));
  el("sentence").focus(); questionAt = performance.now();
}
function submit(index) {
  session.answer(index);
  session.history.at(-1).elapsedMs = Math.round(performance.now() - questionAt);
  advance();
}
el("begin").onclick = () => {
  session = new PhraseCat.Session(bank);
  el("start").hidden = true; el("quiz").hidden = false; advance();
};
el("skip").onclick = () => submit(null);
document.addEventListener("keydown", e => {
  if (el("quiz").hidden || e.repeat || e.isComposing || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !/^[0-4]$/.test(e.key)) return;
  e.preventDefault(); submit(e.key === "0" ? null : Number(e.key) - 1);
});
el("record").onclick = () => {
  const a = document.createElement("a"), url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type:"application/json"}));
  a.href = url; a.download = "phrase-cat-record.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
