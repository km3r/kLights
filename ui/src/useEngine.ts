import { useCallback, useEffect, useRef, useState } from "react";
import type { Command, ConnectionStatus, EngineState } from "./types";

/**
 * The connection to the engine.
 *
 * Reconnects on its own, forever. A lighting console that needs a page refresh
 * after the laptop's wifi blips is a console someone stops trusting mid-set, so
 * the socket is treated as unreliable by default rather than as an error case.
 *
 * State is server-authoritative: the UI never predicts. A tap that has not come
 * back from the engine is a tap that has not happened, and showing it as done
 * would be a lie the moment a command is dropped. The cost is up to one
 * broadcast period (100 ms) of latency on a button, which is invisible; the
 * benefit is that two phones can never disagree about what is live.
 */

const RECONNECT_MIN = 400;
const RECONNECT_MAX = 4000;

function socketUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

function clientName(): string {
  const stored = localStorage.getItem("cosmos.name");
  if (stored) return stored;
  // Something recognisable in the presence list without asking anyone to type.
  const guess = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)
    ? "phone" : "laptop";
  const name = `${guess}-${Math.random().toString(36).slice(2, 5)}`;
  localStorage.setItem("cosmos.name", name);
  return name;
}

export function useEngine() {
  const [state, setState] = useState<EngineState | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [name, setNameState] = useState<string>(clientName);
  const socket = useRef<WebSocket | null>(null);
  const backoff = useRef(RECONNECT_MIN);
  const alive = useRef(true);
  const nameRef = useRef(name);
  nameRef.current = name;

  useEffect(() => {
    alive.current = true;

    const connect = () => {
      if (!alive.current) return;
      setStatus("connecting");
      const ws = new WebSocket(socketUrl());
      socket.current = ws;

      ws.onopen = () => {
        backoff.current = RECONNECT_MIN;
        setStatus("open");
        ws.send(JSON.stringify({ type: "hello", name: nameRef.current }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "state") setState(msg as EngineState);
      };
      ws.onclose = () => {
        setStatus("closed");
        socket.current = null;
        if (!alive.current) return;
        // Exponential backoff, capped low. This is a LAN; a long backoff after
        // a two-second wifi blip means staring at a dead console.
        setTimeout(connect, backoff.current);
        backoff.current = Math.min(backoff.current * 2, RECONNECT_MAX);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      alive.current = false;
      socket.current?.close();
    };
  }, []);

  const send = useCallback((command: Command) => {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(command));
  }, []);

  const setName = useCallback((next: string) => {
    localStorage.setItem("cosmos.name", next);
    setNameState(next);
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "hello", name: next }));
    }
  }, []);

  return { state, status, send, name, setName };
}

/**
 * Keep the screen awake.
 *
 * Carried over from the old console because it mattered there: a phone that
 * sleeps mid-set has to be woken, unlocked and re-navigated before it can do
 * anything, which is a long time when a beam is in someone's eyes. The lock is
 * re-acquired on visibility change, since the browser drops it whenever the tab
 * is backgrounded.
 */
export function useWakeLock(enabled: boolean) {
  const lock = useRef<WakeLockSentinel | null>(null);

  useEffect(() => {
    if (!enabled || !("wakeLock" in navigator)) return;
    let cancelled = false;

    const acquire = async () => {
      try {
        if (cancelled || document.visibilityState !== "visible") return;
        lock.current = await navigator.wakeLock.request("screen");
      } catch {
        /* denied or unsupported; nothing to do but carry on */
      }
    };
    const onVisible = () => { if (document.visibilityState === "visible") acquire(); };

    acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      lock.current?.release().catch(() => {});
      lock.current = null;
    };
  }, [enabled]);
}
