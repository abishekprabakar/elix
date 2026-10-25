import { FIXTURES, getToken } from './client';

export type ElixEventKind = 'audit' | 'work_item' | 'journal' | 'policy' | 'period' | 'scenario' | 'eval' | 'ping';
export interface ElixEvent {
  kind: ElixEventKind;
  data: unknown;
}
type Handler = (e: ElixEvent) => void;

const handlers = new Set<Handler>();
let source: EventSource | null = null;
let retry: number | null = null;
let connectedToken = '';
let status: 'connecting' | 'open' | 'closed' = 'closed';
const statusHandlers = new Set<(s: typeof status) => void>();

export function subscribe(h: Handler): () => void {
  handlers.add(h);
  return () => handlers.delete(h);
}

export function onStatus(h: (s: typeof status) => void): () => void {
  statusHandlers.add(h);
  h(status);
  return () => statusHandlers.delete(h);
}

function setStatus(s: typeof status) {
  status = s;
  statusHandlers.forEach((h) => h(s));
}

/** Used by the fixture mock and by optimistic local updates. */
export function emit(e: ElixEvent) {
  handlers.forEach((h) => {
    try {
      h(e);
    } catch (err) {
      console.error(err);
    }
  });
}

const KINDS: ElixEventKind[] = ['audit', 'work_item', 'journal', 'policy', 'period', 'scenario', 'eval', 'ping'];

export function connect() {
  if (FIXTURES) {
    setStatus('open');
    return;
  }
  const token = getToken();
  if (source && connectedToken === token) return;
  disconnect();
  connectedToken = token;
  setStatus('connecting');
  try {
    source = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
  } catch {
    scheduleRetry();
    return;
  }
  source.onopen = () => setStatus('open');
  source.onerror = () => {
    disconnect();
    scheduleRetry();
  };
  for (const k of KINDS) {
    source.addEventListener(k, (msg) => {
      let data: unknown = null;
      try {
        data = JSON.parse((msg as MessageEvent).data);
      } catch {
        data = (msg as MessageEvent).data;
      }
      emit({ kind: k, data });
    });
  }
}

function scheduleRetry() {
  setStatus('connecting');
  if (retry) window.clearTimeout(retry);
  retry = window.setTimeout(() => {
    retry = null;
    connect();
  }, 3000);
}

export function disconnect() {
  if (source) {
    source.close();
    source = null;
  }
  setStatus('closed');
}
