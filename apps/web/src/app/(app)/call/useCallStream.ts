"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ScreenPop } from "@fieldops/shared";

export type StreamState = "connecting" | "watching" | "reconnecting" | "refused";

/**
 * Holds the office's socket open and hands back every screen pop that arrives.
 *
 * Four things here are not obvious and all of them cost something to find out.
 *
 * **The socket is same-origin.** `/api/calls/stream`, through the same Next
 * rewrite as every other call, so the browser treats it as belonging to this
 * site and sends the session cookie. Pointed straight at the API on another
 * port it would be a cross-site request, `SameSite=Lax` would strip the cookie,
 * and the server would close it as unauthenticated — which looks exactly like
 * a bug in the server.
 *
 * **A refusal is a close code, not an error.** The handshake has to be accepted
 * before the server can say no, so "your role may not watch calls" arrives as
 * 1008 on an open socket. Reconnecting after that would be a loop, so `refused`
 * is terminal.
 *
 * **Reconnection backs off.** A dropped connection on a train retries, but a
 * server that is down should not be asked twenty times a second.
 *
 * **Pops accumulate newest-first and are capped.** A screen pop is worth having
 * for a minute; a page left open all day should not become a list of four
 * hundred of them.
 */
const MAX_POPS = 20;
const BACKOFF_MS = [500, 1000, 2000, 5000, 10_000];

export function useCallStream(enabled: boolean) {
  const [state, setState] = useState<StreamState>("connecting");
  const [pops, setPops] = useState<ScreenPop[]>([]);
  const socketRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dismiss = useCallback((callId: string) => {
    setPops((current) => current.filter((pop) => pop.call.callId !== callId));
  }, []);

  useEffect(() => {
    if (!enabled) return;

    let closed = false;

    function connect() {
      if (closed) return;

      const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${scheme}//${window.location.host}/api/calls/stream`);
      socketRef.current = socket;

      socket.onmessage = (event) => {
        const message = JSON.parse(event.data as string) as
          | { type: "ready" }
          | { type: "ping" }
          | { type: "call"; pop: ScreenPop };

        if (message.type === "ready") {
          attemptRef.current = 0;
          setState("watching");
          return;
        }
        if (message.type === "call") {
          setPops((current) => [message.pop, ...current].slice(0, MAX_POPS));
        }
      };

      socket.onclose = (event) => {
        if (closed) return;
        socketRef.current = null;

        // 1008 is the server declining this employee. Retrying would ask the
        // same question at the same rate forever.
        if (event.code === 1008) {
          setState("refused");
          return;
        }

        setState("reconnecting");
        const wait = BACKOFF_MS[Math.min(attemptRef.current, BACKOFF_MS.length - 1)];
        attemptRef.current += 1;
        timerRef.current = setTimeout(connect, wait);
      };
    }

    connect();

    return () => {
      closed = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [enabled]);

  return { state, pops, dismiss };
}
