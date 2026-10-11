import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';
import { anonymousHistory, saveAnonymousTurn, clearAnonymousHistory, migrateBrowserHistory } from '../src/lib/browser-history.ts';

const storage = new Map();
globalThis.localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: key => storage.delete(key),
};
let pending = Promise.resolve();
Object.defineProperty(globalThis.navigator, 'locks', { value: {
  request: (_key, task) => {
    const result = pending.then(task);
    pending = result.catch(() => {});
    return result;
  },
} });
beforeEach(() => storage.clear());
const turn = () => ({ request_id: crypto.randomUUID(), question: 'Original question',
  answer: { text: 'Full answer', sources: [] }, created: 123 });

test('restores original text, deduplicates retries, and clears anonymous history', async () => {
  const saved = turn();
  await saveAnonymousTurn(saved);
  await saveAnonymousTurn(saved);
  assert.deepEqual(anonymousHistory(), [saved]);
  await clearAnonymousHistory();
  assert.deepEqual(anonymousHistory(), []);
});

test('failed migration retains data and binds retries to the same account', async () => {
  const saved = turn();
  await saveAnonymousTurn(saved);
  await assert.rejects(migrateBrowserHistory('patient-1', async () => { throw Error('offline'); }));
  assert.equal(storage.size, 1);
  assert.deepEqual(anonymousHistory(), []);
  await migrateBrowserHistory('patient-2', async () => assert.fail('Wrong account received history'));
  await migrateBrowserHistory('patient-1', async turns => assert.deepEqual(turns, [saved]));
  assert.equal(storage.size, 0);
});

test('concurrent saves retain both turns and migration clears only after all uploads', async () => {
  await Promise.all([saveAnonymousTurn(turn()), saveAnonymousTurn(turn())]);
  assert.equal(anonymousHistory().length, 2);
  let uploads = 0;
  await migrateBrowserHistory('patient', async turns => {
    uploads++;
    assert.equal(turns.length, 1);
    assert.equal(storage.size, 1);
  });
  assert.equal(uploads, 2);
  assert.equal(storage.size, 0);
});
