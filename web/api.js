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
