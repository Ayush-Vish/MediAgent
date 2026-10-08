export interface SSEEvent { event: string; data: unknown }

// Handles UTF-8 and SSE frames split across arbitrary network chunks.
export async function readSSE(body: ReadableStream<Uint8Array>, onEvent: (event: SSEEvent) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  function consume() {
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join("\n")) });
    }
    if (buffer.length > 131072) throw new Error("Invalid response stream.");
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      consume();
    }
    buffer += decoder.decode();
    consume();
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
