const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../extension/background.js'), 'utf8');
const version = JSON.parse(fs.readFileSync(path.join(__dirname, '../extension/manifest.json'))).version_name;
const product = {ok:true, app:'adaptive-subs', version, ext:2, setup:'/setup', profile:'/profile', key:true};

function worker(fetcher, initial = null) {
  let stored = {base:initial};
  const calls = [];
  const context = vm.createContext({URL, AbortSignal, Date, Promise,
    fetch:async (url, options) => {calls.push({url, options}); return fetcher(url, options);},
    chrome:{storage:{session:{get:async () => stored, set:async value => {stored = value;}}},
      runtime:{onMessage:{addListener:() => {}}, getManifest:() => ({version_name:version})}}});
  vm.runInContext(source, context);
  return {context, calls, run: code => vm.runInContext(code, context), stored:() => stored};
}
const response = (value, status=200) => new Response(JSON.stringify(value), {status});

test('discovery rejects generic health, wrong product, bad status, and unsafe response links', async () => {
  for (const [value,status] of [[{ok:true},200],[{...product,app:'other'},200], [product,503],
      [{...product,ok:'yes'},200], [{...product,ext:true},200], [{...product,version:'wrong version'},200],
      [{...product,setup:'https://outside.invalid'},200], [{...product,profile:'//outside.invalid'},200],
      [{...product,padding:'x'.repeat(16384)},200]]) {
    const w = worker(() => response(value,status));
    assert.equal(await w.run("health('http://127.0.0.1:8765')"), null);
    assert.equal(w.calls[0].options.redirect, 'error');
  }
  for (const value of [product, Object.fromEntries(Object.entries(product).filter(([k]) => k !== 'app'))]) {
    const w = worker(() => response(value));
    assert.equal((await w.run("health('http://127.0.0.1:8765')")).version,version);
  }
});

test('a persisted foreign or malformed address is never probed', async () => {
  for (const address of ['https://outside.invalid', 'http://127.0.0.1:8765@outside.invalid',
      'http://user@127.0.0.1:8765', 'http://127.0.0.1:8765/path', 'http://127.0.0.1:8765/?query=1']) {
    const w = worker(() => response(product));
    w.context.address = address;
    assert.equal(await w.run('health(address)'),null);
    assert.equal(w.calls.length,0);
  }
});

test('cached unrelated service is skipped and profile writes only use the discovered product', async () => {
  const w = worker(url => {
    if (url === 'http://127.0.0.1:8766/api/health') return response(product);
    if (url === 'http://127.0.0.1:8766/api/mark') return response({ok:true});
    return response({ok:true});
  }, 'http://127.0.0.1:8765');
  assert.equal((await w.run("handle({type:'mark',payload:{key:'bank',state:'known'}})")).ok,true);
  assert.equal(w.stored().base,'http://127.0.0.1:8766');
  const posts = w.calls.filter(c => c.options.method === 'POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,'http://127.0.0.1:8766/api/mark');
  assert.equal(JSON.parse(posts[0].options.body).key,'bank');
});

test('health reports mismatched versions without pretending they match', async () => {
  const w = worker(() => response({...product,version:'0.3.0'}));
  const result = await w.run("handle({type:'health'})");
  assert.equal(result.ok,true);
  assert.equal(result.versionMismatch,true);
  assert.equal(result.version,'0.3.0');
});
