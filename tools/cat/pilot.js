"use strict";
const el = id => document.getElementById(id);
const data = JSON.parse(el("data").textContent);
let session, result;
el("prior").max = data.difficulties.b_initial.length - 1;
el("prior").value = Math.min(5000, Number(el("prior").max));

function advance() {
  const question = session.next();
  if (!question) {
    result = session.result();
    const { expectedVocabulary, theta, se } = result.fits.b_initial;
    el("quiz").hidden = true;
    el("done").hidden = false;
    el("outcome").textContent = `共完成 ${result.responses.length} 题。按本表估计认识约 ${expectedVocabulary.toLocaleString("zh-CN")} 个词元。`;
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
    button.onclick = () => { session.answer(i); advance(); };
    return button;
  }));
  el("word").focus();
}

el("begin").onclick = () => {
  if (!el("prior").reportValidity()) return;
  try { session = new WordCat.Session(data, el("prior").valueAsNumber); }
  catch (error) { el("error").textContent = error.message; return; }
  el("start").hidden = true;
  el("quiz").hidden = false;
  advance();
};
el("skip").onclick = () => { session.answer(null); advance(); };

function save(name, content) {
  const link = document.createElement("a");
  const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2) + "\n"], {type: "application/json"}));
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
el("parameters").onclick = () => save("word-cat-parameters.json", result.parameters);
el("record").onclick = () => save("word-cat-record.json", result);
