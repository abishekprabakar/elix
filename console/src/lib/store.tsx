import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, FIXTURES, setToken } from '../api/client';
import { connect, subscribe, type ElixEvent } from '../api/events';
import type { Actor, AuditEvent, ChainCheck, SessionInfo, World } from '../api/types';

export const PEOPLE: { token: string; id: string; label: string }[] = [
  { token: 'tok_priya', id: 'human:priya', label: 'Controller' },
  { token: 'tok_dev', id: 'human:dev', label: 'Head of ML' },
  { token: 'tok_maya', id: 'human:maya', label: 'Head of Growth' },
  { token: 'tok_sam', id: 'human:sam', label: 'Staff accountant' },
  { token: 'tok_jordan', id: 'human:jordan', label: 'Auditor' },
];

export type Theme = 'system' | 'light' | 'dark';

interface Toast {
  id: number;
  text: string;
  tone: 'ok' | 'deny' | 'neutral';
}

interface Store {
  token: string;
  setActing: (token: string) => void;
  session: SessionInfo | null;
  world: World | null;
  worldError: ApiError | null;
  reloadWorld: () => void;
  period: string;
  setPeriod: (p: string) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  chain: ChainCheck | null;
  refreshChain: () => Promise<ChainCheck | null>;
  /** Increments on any server change so views can refetch without flashing. */
  version: number;
  liveEvents: AuditEvent[];
  freshSeqs: Set<number>;
  toasts: Toast[];
  toast: (text: string, tone?: Toast['tone']) => void;
  /** Refetch every live view, used after an action so nothing waits on SSE. */
  refresh: () => void;
  actorName: (id: string | null | undefined) => string;
  actor: (id: string | null | undefined) => Actor | undefined;
  now: string;
  fixtures: boolean;
}

const Ctx = createContext<Store | null>(null);

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, v: string) {
  try {
    window.localStorage.setItem(key, v);
  } catch {
    /* storage can be blocked */
  }
}

function initialToken(): string {
  const params = new URLSearchParams(window.location.search);
  const as = params.get('as');
  if (as) {
    const p = PEOPLE.find((x) => x.token === `tok_${as}` || x.id === `human:${as}`);
    if (p) return p.token;
  }
  const saved = read('elix.acting');
  return PEOPLE.some((p) => p.token === saved) ? (saved as string) : 'tok_priya';
}
function initialTheme(): Theme {
  const params = new URLSearchParams(window.location.search);
  const t = params.get('theme') ?? read('elix.theme');
  return t === 'dark' || t === 'system' ? t : 'light';
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [token, setTok] = useState(() => {
    const t = initialToken();
    setToken(t);
    return t;
  });
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [world, setWorld] = useState<World | null>(null);
  const [worldError, setWorldError] = useState<ApiError | null>(null);
  const [period, setPeriodState] = useState<string>(() => new URLSearchParams(window.location.search).get('period') ?? '');
  const [theme, setThemeState] = useState<Theme>(initialTheme);
  const [chain, setChain] = useState<ChainCheck | null>(null);
  const [version, setVersion] = useState(0);
  const [liveEvents, setLive] = useState<AuditEvent[]>([]);
  const [freshSeqs, setFresh] = useState<Set<number>>(new Set());
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [worldTick, setWorldTick] = useState(0);
  const [latestAt, setLatestAt] = useState<string>('');
  const bump = useRef<number | null>(null);
  const chainTimer = useRef<number | null>(null);
  const refreshChainRef = useRef<() => void>(() => {});

  useEffect(() => {
    document.documentElement.dataset.theme = theme === 'system' ? '' : theme;
    if (theme === 'system') delete document.documentElement.dataset.theme;
  }, [theme]);

  const loadWorld = useCallback(async () => {
    try {
      const w = await api<World>('/api/world');
      setWorld(w);
      setWorldError(null);
      setPeriodState((p) => p || w.periods.find((x) => x.status === 'open')?.id || w.periods[w.periods.length - 1]?.id || '');
    } catch (e) {
      setWorldError(e as ApiError);
    }
  }, []);

  useEffect(() => {
    loadWorld();
  }, [loadWorld, worldTick]);

  useEffect(() => {
    let alive = true;
    setSession(null);
    api<SessionInfo>('/api/session')
      .then((s) => alive && setSession(s))
      .catch(() => alive && setSession(null));
    return () => {
      alive = false;
    };
  }, [token, worldTick]);

  const refreshChain = useCallback(async () => {
    try {
      const c = await api<ChainCheck>('/api/audit/verify');
      setChain(c);
      return c;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    refreshChain();
  }, [refreshChain, worldTick]);

  // The world clock: company.today may be a bare date, so the latest audit event marks "now".
  useEffect(() => {
    let alive = true;
    api<{ events: AuditEvent[] }>('/api/audit?limit=1')
      .then((r) => {
        const at = r.events?.[0]?.at;
        if (alive && at) setLatestAt((t) => (at > t ? at : t));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [worldTick, token]);
  refreshChainRef.current = () => {
    refreshChain();
  };

  useEffect(() => {
    connect();
    const off = subscribe((e: ElixEvent) => {
      if (e.kind === 'ping') return;
      if (e.kind === 'audit') {
        const ev = e.data as AuditEvent;
        if (ev && typeof ev.seq === 'number') {
          setLive((l) => (l.some((x) => x.seq === ev.seq) ? l : [ev, ...l].slice(0, 60)));
          setFresh((s) => new Set(s).add(ev.seq));
          window.setTimeout(() => setFresh((s) => {
            const n = new Set(s);
            n.delete(ev.seq);
            return n;
          }), 1400);
          setLatestAt((t) => (ev.at > t ? ev.at : t));
        }
      }
      if (e.kind === 'period') setWorldTick((t) => t + 1);
      // coalesce bursts of events into one refetch
      if (bump.current) window.clearTimeout(bump.current);
      bump.current = window.setTimeout(() => {
        bump.current = null;
        setVersion((v) => v + 1);
      }, 250);
      if (e.kind === 'audit' && !chainTimer.current) {
        chainTimer.current = window.setTimeout(() => {
          chainTimer.current = null;
          refreshChainRef.current();
        }, 2000);
      }
    });
    return off;
  }, [token]);

  const setActing = useCallback((t: string) => {
    setToken(t);
    setTok(t);
    write('elix.acting', t);
    connect();
    setVersion((v) => v + 1);
  }, []);

  const setPeriod = useCallback((p: string) => setPeriodState(p), []);
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    write('elix.theme', t);
  }, []);

  const toast = useCallback((text: string, tone: Toast['tone'] = 'neutral') => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const actor = useCallback((id: string | null | undefined) => world?.actors.find((a) => a.id === id), [world]);
  const actorName = useCallback(
    (id: string | null | undefined) => {
      if (!id) return '';
      const a = world?.actors.find((x) => x.id === id);
      if (a) return a.name;
      if (id === 'system:policy') return 'Elix policy engine';
      return id.replace(/^(human|agent|system):/, '');
    },
    [world],
  );

  const now = useMemo(() => {
    if (world?.now && (!latestAt || world.now >= latestAt)) return world.now;
    const today = world?.company.today ?? '';
    const t = today.length === 10 ? `${today}T00:00:00Z` : today || new Date().toISOString();
    return latestAt && latestAt > t ? latestAt : t;
  }, [world, latestAt]);

  const value: Store = {
    token,
    setActing,
    session,
    world,
    worldError,
    reloadWorld: () => setWorldTick((t) => t + 1),
    period,
    setPeriod,
    theme,
    setTheme,
    chain,
    refreshChain,
    version,
    liveEvents,
    freshSeqs,
    toasts,
    toast,
    refresh: () => {
      setVersion((v) => v + 1);
      refreshChain();
    },
    actor,
    actorName,
    now,
    fixtures: FIXTURES,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error('StoreProvider missing');
  return s;
}

/** Fetch with stale while revalidate: keeps old data visible while refetching on server events. */
export function useApi<T>(path: string | null, opts: { live?: boolean } = {}) {
  const { version, token } = useStore();
  const live = opts.live ?? true;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [n, setN] = useState(0);
  const lastPath = useRef<string | null>(null);

  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    let alive = true;
    if (lastPath.current !== path) {
      setLoading(true);
      setData(null);
    }
    lastPath.current = path;
    api<T>(path)
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (!alive) return;
        setError(e as ApiError);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, token, n, live ? version : 0]);

  return { data, error, loading, reload: () => setN((x) => x + 1), setData };
}
