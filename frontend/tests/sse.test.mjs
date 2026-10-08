import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readSSE } from '../src/lib/sse.ts';

test('SSE parser handles split UTF-8, CRLF, comments, and multiline data', async () => {
  const bytes = new TextEncoder().encode(': ping\r\n\r\nevent: draft\r\ndata: {"text":\r\ndata: "Hello 🩺"}\r\n\r\nevent: done\ndata: {"text":"Complete"}\n\n');
  const body = new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
    controller.close();
  } });
  const events = [];
  await readSSE(body, event => events.push(event));
  assert.deepEqual(events, [{ event: 'draft', data: { text: 'Hello 🩺' } }, { event: 'done', data: { text: 'Complete' } }]);
});

test('SSE parser releases the stream when an error event handler throws', async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('event: error\ndata: {"error":"Unavailable"}\n\n'));
  }, cancel() { cancelled = true; } });
  await assert.rejects(readSSE(body, ({ data }) => { throw new Error(data.error); }), /Unavailable/);
  assert.equal(cancelled, true);
  assert.equal(body.locked, false);
});
