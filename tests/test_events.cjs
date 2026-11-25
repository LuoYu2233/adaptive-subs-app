const assert = require('node:assert/strict');
const { test } = require('node:test');
const Events = require('../app/web/assets/api-events.js');

test('player sends protection header, preserves cursor and closes at completion', async () => {
  const requests = [];
  const batches = [
    { events: [['meta', { title: 'test' }]], next: 1 },
    { events: [['ann', { 0: [] }], ['done', { seconds: 2 }]], next: 3 },
  ];
  const stream = new Events('video&id', async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => batches.shift() };
  });
  const seen = [];
  for (const type of ['meta', 'ann', 'done']) stream.addEventListener(type, () => seen.push(type));
  await stream.finished;
  assert.deepEqual(seen, ['meta', 'ann', 'done']);
  assert.equal(requests[0].options.headers['X-Adaptive-Subs'], '1');
  assert.match(requests[0].url, /v=video%26id&since=0/);
  assert.match(requests[1].url, /since=1/);
  assert.equal(stream.readyState, 2);
});

test('closing a video prevents stale results from reaching its listeners', async () => {
  let finish;
  const stream = new Events('old', () => new Promise(resolve => { finish = resolve; }));
  let called = false;
  stream.addEventListener('meta', () => { called = true; });
  stream.close();
  finish({ ok: true, json: async () => ({ events: [['meta', {}]], next: 1 }) });
  await stream.finished;
  assert.equal(called, false);
});
