/* Local interactive examples use exactly the same selection rules as the player and extension. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const examples = [
    { label:"01 · 去银行办事", text:"The bank is closed today.", zh:"银行今天不营业。", span:"bank", key:"bank", gloss:"银行", kind:"word", lv:2500 },
    { label:"02 · 沿河散步", text:"We sat on the bank of the river.", zh:"我们坐在河岸边。", span:"bank", key:"bank", gloss:"河岸", kind:"sense", lv:6000 },
    { label:"03 · 项目开始受到关注", text:"The project took off after the launch.", zh:"项目上线后迅速发展起来。", span:"took off", key:"take off", gloss:"迅速发展", kind:"phrase", lv:6000 },
  ].map(c => ({...c, notes:[{s:c.text.indexOf(c.span), e:c.text.indexOf(c.span)+c.span.length, key:c.key, zh:c.gloss, kind:c.kind, lv:c.lv}]}));
  let level=3000, mode="adaptive", selected=1;
  const known=new Set(), unknown=new Set();
  function render() {
    $("#levelValue").textContent = level.toLocaleString("en-US")+" 词";
    const counts=new Map(); let hints=0;
    $("#examples").innerHTML=examples.map((c,i) => {
      const n=c.notes[0], show=mode==="adaptive" && AdaptiveLearning.select(c.notes, known, unknown, level, {perLine:2,repeat:3},counts).has(0);
      hints+=Number(show);
      return `<article class="example"><div class="label">${esc(c.label)}</div><div class="sentence">${esc(c.text.slice(0,n.s))}<button type="button" data-note="${i}" class="term${selected===i?" selected":""}" aria-label="查看 ${esc(c.span)} 在这一句中的意思" aria-pressed="${selected===i}">${show?`<span class="gloss">${esc(n.zh)}</span>`:""}${esc(c.span)}</button>${esc(c.text.slice(n.e))}</div>${mode==="bilingual"?`<p class="translation">${esc(c.zh)}</p>`:""}</article>`;
    }).join("");
    $("#density").textContent=mode==="adaptive"?`这三句显示 ${hints} 处提示。水平越高，自动提示越少。`:mode==="plain"?"纯英文模式保留原句，也可以点词查看意思。":"中英对照显示整句翻译，可以比较阅读体验。";
    const n=examples[selected].notes[0], id=AdaptiveLearning.key(n), status=AdaptiveLearning.state(n,known,unknown,level);
    $("#meaningTitle").textContent=n.key; $("#meaningZh").textContent=n.zh;
    $("#reason").textContent=known.has(id)?"已记住你认识这个意思；同一个词的其他意思仍独立判断。":unknown.has(id)?"已记住这个意思还不熟；在自适应模式下会继续提示。":status==="hard"?"这个用法高于当前设置的水平，所以会自动提示。":"这个用法按当前水平不自动提示；不熟悉时可以单独标记。";
    $("#known").setAttribute("aria-pressed",String(known.has(id)));
    $("#unknown").setAttribute("aria-pressed",String(unknown.has(id)));
  }
  $("#examples").addEventListener("click", e => {
    const button=e.target.closest("[data-note]"); if(!button)return;
    selected=Number(button.dataset.note);render();$("#meaningTitle").focus({preventScroll:true});
    if(matchMedia("(max-width:780px)").matches)$(".meaning").scrollIntoView({behavior:matchMedia("(prefers-reduced-motion:reduce)").matches?"auto":"smooth",block:"center"});
  });
  $("#level").addEventListener("input",e=>{level=Number(e.target.value);render();});
  document.querySelectorAll('[name="mode"]').forEach(input=>input.addEventListener("change",()=>{mode=input.value;render();}));
  for(const action of ["known","unknown","clear"]) $("#"+action).addEventListener("click",()=>{
    const n=examples[selected].notes[0], id=AdaptiveLearning.key(n);known.delete(id);unknown.delete(id);
    if(action==="known")known.add(id);if(action==="unknown")unknown.add(id);
    render();$("#message").textContent=`${n.key}（${n.zh}）：${action==="known"?"已认识":action==="unknown"?"还不熟悉":"按水平判断"}`;
  });
  $("#reset").addEventListener("click",()=>{
    known.clear();unknown.clear();level=3000;mode="adaptive";selected=1;$("#level").value="3000";
    $('[name="mode"][value="adaptive"]').checked=true;render();$("#message").textContent="已重置体验";
  });
  render();
})();
