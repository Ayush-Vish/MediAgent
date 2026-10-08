import assert from 'node:assert/strict';
import test from 'node:test';
import { ChatAnswerSchema, parseSessionHistory } from '../src/types/api.ts';

test('nullable backend metadata preserves restored answer and citations', () => {
  const answer = {
    text: 'Book using the patient portal. [S1]',
    severity: null, review_id: null,
    sources: [{ id: 'S1', title: 'Patient portal', url: 'https://example.org', checked_at: '2026-10-08', page: null }],
  };
  assert.equal(ChatAnswerSchema.safeParse(answer).success, true);
  const history = parseSessionHistory({ turns: [{ question: 'Booking?', answer, created: 123 }] });
  assert.equal(history.turns[0].answer.text, answer.text);
  assert.equal(history.turns[0].answer.sources.length, 1);
  assert.equal(history.turns[0].created, 123);
});

test('legacy metadata never causes object coercion', () => {
  const history = parseSessionHistory({ turns: [
    { question: 'Question', answer: { text: '**Earlier answer**', sources: 'bad-data' } },
    { question: 'Question', answer: { unexpected: true } },
  ] });
  assert.equal(history.turns[0].answer.text, '**Earlier answer**');
  assert.match(history.turns[1].answer.text, /could not be restored/);
  assert.equal(JSON.stringify(history).includes('[object Object]'), false);
});

test('invalid history payload is rejected at the boundary', () => {
  assert.throws(() => parseSessionHistory({ turns: 'invalid' }));
});
