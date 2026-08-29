"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  CallScript,
  CallSuggestion,
  SkippedUtterance,
  Utterance,
} from "@fieldops/shared";

export type AssistState = "idle" | "connecting" | "listening" | "closed" | "refused";

/** A live partial: the current guess at what is being said. */
type Partial = { speaker: "caller" | "agent"; text: string } | null;

/**
 * The other end of `/calls/assist`.
 *
 * The socket is not opened until a call starts, and it is closed when one ends.
 * A page left open all day should not hold a listening socket for a call that
 * is not happening — and on the server that socket is a polling task.
 *
 * Three things worth noting.
 *
 * **The replay is here, in the browser.** The scripted call is a list of
 * fragments and delays; sending them over the same socket a transcription
 * service would use means the pause detector, the classifier, retrieval and
 * generation all run for real. Only the microphone is fake. Driving it from the
 * server instead would have made the demo prove nothing about the ingest path.
 *
 * **Suggestions are keyed by id, not appended.** Each message carries the whole
 * text so far, so the one already on screen is replaced rather than added to —
 * see `CallSuggestion` for why the server does not send deltas.
 *
 * **Refusals are terminal.** 1008 means the server declined this employee.
 * Reconnecting would ask the same question forever.
 */
export function useAssist() {
  const [state, setState] = useState<AssistState>("idle");
  const [utterances, setUtterances] = useState<Utterance[]>([]);
  const [partial, setPartial] = useState<Partial>(null);
  const [suggestions, setSuggestions] = useState<CallSuggestion[]>([]);
  const [skipped, setSkipped] = useState<SkippedUtterance[]>([]);
  const [capped, setCapped] = useState(false);

  const socket = useRef<WebSocket | null>(null);
  const timers = useRef<Array<ReturnType<typeof setTimeout>>>([]);

  const stop = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    socket.current?.close();
    socket.current = null;
    setPartial(null);
    setState((current) => (current === "refused" ? current : "idle"));
  }, []);

  // A page navigated away from must not leave a socket and a queue of timers
  // behind it.
  useEffect(() => stop, [stop]);

  const start = useCallback(
    (script: CallScript | null) => {
      stop();
      setUtterances([]);
      setSuggestions([]);
      setSkipped([]);
      setCapped(false);
      setPartial(null);
      setState("connecting");

      const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${scheme}//${window.location.host}/api/calls/assist`);
      socket.current = ws;

      ws.onmessage = (event) => {
        const message = JSON.parse(event.data as string);

        switch (message.type) {
          case "ready":
            setState("listening");
            if (script) replay(ws, script, timers.current);
            break;
          case "partial":
            setPartial({ speaker: message.speaker, text: message.text });
            break;
          case "utterance":
            setPartial(null);
            setUtterances((current) => [
              ...current,
              { speaker: message.speaker, text: message.text },
            ]);
            break;
          case "skipped":
            setSkipped((current) => [
              ...current,
              { id: `${current.length}-${message.reason}`, reason: message.reason },
            ]);
            break;
          case "suggestion":
            setSuggestions((current) => {
              const next = current.filter((item) => item.id !== message.id);
              return [...next, message as CallSuggestion];
            });
            break;
          case "capped":
            setCapped(true);
            break;
        }
      };

      ws.onclose = (event) => {
        socket.current = null;
        setState(event.code === 1008 ? "refused" : "closed");
      };
    },
    [stop],
  );

  const attach = useCallback((customerId: string | null) => {
    socket.current?.send(JSON.stringify({ type: "customer", customerId }));
  }, []);

  return { state, utterances, partial, suggestions, skipped, capped, start, stop, attach };
}

/**
 * Send a scripted call at its own pace.
 *
 * Cumulative timeouts rather than a chain of `setTimeout`s inside each other:
 * one list to cancel when somebody hangs up, and the schedule does not drift by
 * the cost of each callback.
 */
function replay(
  socket: WebSocket,
  script: CallScript,
  timers: Array<ReturnType<typeof setTimeout>>,
) {
  let elapsed = 0;

  if (script.customerId) {
    socket.send(JSON.stringify({ type: "customer", customerId: script.customerId }));
  }

  for (const fragment of script.fragments) {
    elapsed += fragment.delayMs;
    timers.push(
      setTimeout(() => {
        if (socket.readyState !== WebSocket.OPEN) return;
        socket.send(
          JSON.stringify({
            type: "transcript",
            speaker: fragment.speaker,
            text: fragment.text,
            final: fragment.final,
          }),
        );
      }, elapsed),
    );
  }
}
