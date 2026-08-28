import type { DoneEvent, SourcesEvent, ToolRun } from "@fieldops/shared";

/**
 * Server-sent events over `fetch`.
 *
 * Not `EventSource`, for two reasons: it can only issue a GET, and the question
 * belongs in a request body rather than in a URL that lands in access logs and
 * browser history. Parsing the wire format by hand is about twenty lines, and
 * this is all of them.
 */
export type ChatEvent =
  | { type: "start"; data: { conversationId: string } }
  | { type: "tool"; data: { id: string; name: string } }
  | { type: "tool_done"; data: ToolRun }
  | { type: "sources"; data: SourcesEvent }
  | { type: "delta"; data: { text: string } }
  | { type: "done"; data: DoneEvent }
  | { type: "error"; data: { message: string } };

export async function* askStream(
  question: string,
  conversationId: string | null,
  signal: AbortSignal,
  /** Set to replace an earlier question: everything from it onward is deleted. */
  editMessageId?: string | null,
): AsyncGenerator<ChatEvent> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, conversationId, editMessageId }),
    signal,
  });

  if (!response.ok || !response.body) {
    // A failure before the stream opens is an ordinary HTTP error — the session
    // expired, or the role is not allowed to ask. Once the stream is open the
    // status is already sent and errors arrive as an `error` event instead.
    const body = await response.json().catch(() => null);
    yield {
      type: "error",
      data: { message: body?.error?.message ?? `Request failed (${response.status}).` },
    };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Events are separated by a blank line. A chunk can split one anywhere, so
    // only complete events are taken and the remainder stays buffered.
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseEvent(raw);
      if (parsed) yield parsed;
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function parseEvent(raw: string): ChatEvent | null {
  let name = "";
  const dataLines: string[] = [];

  for (const line of raw.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }

  if (!name || dataLines.length === 0) return null;

  try {
    return { type: name, data: JSON.parse(dataLines.join("\n")) } as ChatEvent;
  } catch {
    return null;
  }
}
