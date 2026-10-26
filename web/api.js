/** Same-origin JSON client. Tokens remain in memory, never localStorage. */
export async function api(path, {method = 'GET', body, token, signal} = {}) {
  const headers = {Accept: 'application/json'};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, {method, headers, credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body), signal});
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.error ?? `Request failed (${response.status}).`);
  if (data === null) throw new Error('The server returned an invalid response.');
  return data;
}

export async function streamChat(body, {onStatus = () => {}, onDraft = () => {}, signal} = {}) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', Accept: 'text/event-stream'},
    credentials: 'same-origin',
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.error ?? `Request failed (${response.status}).`);
  }
  if (!response.body) throw new Error('Response streaming is unavailable.');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer = null;
  const consume = () => {
    let boundary;
    while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      let event = 'message';
      const dataLines = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
      }
      if (!dataLines.length) continue;
      const data = JSON.parse(dataLines.join('\n'));
      if (event === 'status' && typeof data?.text === 'string') onStatus(data.text);
      if (event === 'draft' && typeof data?.text === 'string') onDraft(data.text);
      if (event === 'done' && typeof data?.text === 'string' && Array.isArray(data.sources)) answer = data;
      if (event === 'error') throw new Error(typeof data?.error === 'string' ? data.error : 'Chat request failed.');
    }
    if (buffer.length > 131072) throw new Error('Invalid response stream.');
  };

  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      consume();
    }
    buffer += decoder.decode();
    consume();
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (!answer) throw new Error('The response was interrupted. Please retry.');
  return answer;
}

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function safeLink(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' ? parsed.href : null;
  } catch { return null; }
}
