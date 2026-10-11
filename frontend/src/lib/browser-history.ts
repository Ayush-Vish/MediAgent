import { z } from "zod";

const KEY = "mediagent-anonymous-history-v1";
const TurnSchema = z.object({
  request_id: z.string().uuid(), question: z.string().min(1).max(2000),
  answer: z.object({ text: z.string().min(1).max(100000) }).passthrough(),
  created: z.number().finite().nonnegative(),
});
const HistorySchema = z.object({
  owner: z.string().nullable(), turns: z.array(TurnSchema),
});
type Turn = z.infer<typeof TurnSchema>;

function read() {
  const raw = localStorage.getItem(KEY);
  return raw ? HistorySchema.parse(JSON.parse(raw)) : { owner: null, turns: [] };
}

export function anonymousHistory() {
  const history = read();
  return history.owner === null ? history.turns : [];
}

export async function saveAnonymousTurn(turn: Turn) {
  return navigator.locks.request(KEY, () => {
    const history = read();
    if (history.owner !== null) throw new Error("Sign in to finish saving your earlier conversation first.");
    const valid = TurnSchema.parse(turn);
    const turns = history.turns.filter(t => t.request_id !== valid.request_id);
    localStorage.setItem(KEY, JSON.stringify({ owner: null, turns: [...turns, valid] }));
  });
}

export async function clearAnonymousHistory() {
  return navigator.locks.request(KEY, () => {
    if (read().owner === null) localStorage.removeItem(KEY);
  });
}

export async function migrateBrowserHistory(userId: string, upload: (turns: Turn[]) => Promise<void>) {
  return navigator.locks.request(KEY, async () => {
    const history = read();
    if (!history.turns.length || (history.owner !== null && history.owner !== userId)) return;
    // Bind failed migrations to the first signed-in account. Another account must
    // never receive these messages, even after logout or a network failure.
    localStorage.setItem(KEY, JSON.stringify({ ...history, owner: userId }));
    for (const turn of history.turns) {
      await upload([turn]);
    }
    const current = read();
    if (current.owner === userId) localStorage.removeItem(KEY);
  });
}
