import { useEffect, useRef, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Summary, WorkItemList } from '../api/types';
import { Avatar } from '../components/Bits';
import {
  ElixMark,
  IconActivity,
  IconAgent,
  IconAudit,
  IconChain,
  IconCheck,
  IconChevron,
  IconInbox,
  IconKeyboard,
  IconLedger,
  IconLock,
  IconMenu,
  IconMonitor,
  IconMoon,
  IconPolicy,
  IconProof,
  IconReconcile,
  IconScenario,
  IconSearch,
  IconSun,
  IconToday,
  IconWarn,
} from '../components/Icons';
import { Pill } from '../components/Pill';
import { dateYear, money, periodLabel } from '../lib/format';
import { useKeys } from '../lib/hooks';
import { PEOPLE, useApi, useStore, type Theme } from '../lib/store';
import { DrawerProvider, useDrawer } from './Drawer';

const NAV = [
  { to: '/', label: 'Today', icon: IconToday, end: true },
  { to: '/inbox', label: 'Inbox', icon: IconInbox, count: true },
  { to: '/activity', label: 'Activity', icon: IconActivity },
  { to: '/reconcile', label: 'Reconcile', icon: IconReconcile },
  { to: '/ledger', label: 'Ledger', icon: IconLedger },
  { to: '/policies', label: 'Policies', icon: IconPolicy },
  { to: '/agents', label: 'Agents', icon: IconAgent },
  { to: '/audit', label: 'Audit', icon: IconAudit },
  { to: '/proof', label: 'Proof', icon: IconProof },
];

export function Layout() {
  return (
    <DrawerProvider>
      <Shell />
    </DrawerProvider>
  );
}

function Shell() {
  const navigate = useNavigate();
  const loc = useLocation();
  const [sheet, setSheet] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const { close } = useDrawer();
  const { worldError, world } = useStore();

  useKeys({
    'g i': () => navigate('/inbox'),
    'g t': () => navigate('/'),
    'g a': () => navigate('/activity'),
    'g r': () => navigate('/reconcile'),
    'g l': () => navigate('/ledger'),
    'g p': () => navigate('/policies'),
    'g u': () => navigate('/audit'),
    'g s': () => navigate('/scenarios'),
    '/': (e) => {
      e.preventDefault();
      searchRef.current?.focus();
    },
    '?': () => setSheet((s) => !s),
    Escape: () => {
      setSheet(false);
      close();
    },
  });

  useEffect(() => setMenuOpen(false), [loc.pathname]);

  return (
    <div className="app">
      <TopBar searchRef={searchRef} onMenu={() => setMenuOpen((o) => !o)} />
      <div className="app-body">
        <nav className={`nav${menuOpen ? ' is-open' : ''}`} aria-label="Main">
          <NavItems />
          <button type="button" className="nav-keys" onClick={() => setSheet(true)}>
            <IconKeyboard size={14} /> Keyboard shortcuts
          </button>
        </nav>
        <main className="main" id="main">
          {worldError && !world ? <ApiDown message={worldError.message} /> : <Outlet />}
        </main>
      </div>
      <Footer />
      {sheet && <Shortcuts onClose={() => setSheet(false)} />}
      <Toasts />
    </div>
  );
}

function ApiDown({ message }: { message: string }) {
  const { reloadWorld } = useStore();
  return (
    <div className="page">
      <div className="api-down">
        <IconWarn size={18} />
        <div>
          <h1 className="api-down-title">Elix cannot reach its API</h1>
          <p className="ink-2">{message}</p>
          <p className="ink-2">
            Run <code className="mono">python -m elix demo</code> from the repository root, then try again.
          </p>
          <button type="button" className="btn btn-secondary" onClick={reloadWorld}>
            Try again
          </button>
        </div>
      </div>
    </div>
  );
}

function NavItems() {
  const { session, period, version } = useStore();
  const { data: inbox } = useApi<WorkItemList>(session ? `/api/work-items?inbox=me&limit=1` : null);
  void version;
  void period;
  const count = inbox?.total ?? 0;
  return (
    <>
      <ul className="nav-list">
        {NAV.map((n) => (
          <li key={n.to}>
            <NavLink to={n.to} end={n.end} className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}>
              <n.icon size={16} />
              <span>{n.label}</span>
              {n.count && count > 0 && <span className="nav-count tnum">{count}</span>}
            </NavLink>
          </li>
        ))}
      </ul>
      <ul className="nav-list nav-list-demo">
        <li>
          <NavLink to="/scenarios" className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}>
            <IconScenario size={16} />
            <span>Scenarios</span>
          </NavLink>
        </li>
      </ul>
    </>
  );
}

function TopBar({ searchRef, onMenu }: { searchRef: React.RefObject<HTMLInputElement>; onMenu: () => void }) {
  const { world } = useStore();
  return (
    <header className="topbar">
      <div className="topbar-brand">
        <button type="button" className="btn btn-ghost topbar-menu" onClick={onMenu} aria-label="Menu">
          <IconMenu size={16} />
        </button>
        <NavLink to="/" className="brand" aria-label="Elix home">
          <ElixMark size={20} />
          <span className="brand-word">Elix</span>
        </NavLink>
      </div>
      <div className="topbar-org">
        <span className="org-name">{world?.company.name ?? 'Kestrel Labs'}</span>
        <PeriodPicker />
      </div>
      <Search inputRef={searchRef} />
      <ActingAs />
    </header>
  );
}

function useOutside(ref: React.RefObject<HTMLElement>, onOut: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOut();
    };
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onOut();
    document.addEventListener('mousedown', h);
    document.addEventListener('keydown', k);
    return () => {
      document.removeEventListener('mousedown', h);
      document.removeEventListener('keydown', k);
    };
  }, [ref, onOut, active]);
}

function PeriodPicker() {
  const { world, period, setPeriod, actorName } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false), open);
  const cur = world?.periods.find((p) => p.id === period);
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className="period-btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {cur?.status === 'closed' && <IconLock size={13} />}
        <span>{period ? periodLabel(period) : 'Period'}</span>
        <IconChevron size={14} />
      </button>
      {open && (
        <ul className="menu menu-period" role="listbox">
          {[...(world?.periods ?? [])].reverse().map((p) => (
            <li key={p.id}>
              <button
                type="button"
                role="option"
                aria-selected={p.id === period}
                className={`menu-item${p.id === period ? ' is-selected' : ''}`}
                onClick={() => {
                  setPeriod(p.id);
                  setOpen(false);
                }}
              >
                <span className="menu-item-glyph">{p.status === 'closed' ? <IconLock size={13} /> : <span className="open-dot" />}</span>
                <span className="menu-item-main">
                  <span>{periodLabel(p.id)}</span>
                  <span className="menu-item-sub">{p.status === 'closed' ? `Closed by ${actorName(p.closed_by)} on ${dateYear(p.closed_at)}` : 'Open'}</span>
                </span>
                {p.id === period && <IconCheck size={14} />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Search({ inputRef }: { inputRef: React.RefObject<HTMLInputElement> }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<WorkItemList['items']>([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { open: openDrawer } = useDrawer();
  const { period } = useStore();
  useOutside(ref, () => setOpen(false), open);

  useEffect(() => {
    const term = q.trim().toLowerCase();
    if (!term) {
      setResults([]);
      return;
    }
    let alive = true;
    const t = window.setTimeout(async () => {
      try {
        const r = await api<WorkItemList>(`/api/work-items?limit=500${period ? '' : ''}`);
        if (!alive) return;
        const hits = r.items.filter((i) => {
          const inv = (i.data?.invoice as { invoice_number?: string } | undefined)?.invoice_number ?? '';
          return [i.id, i.title, i.vendor_id ?? '', i.requested_by, inv, i.purpose ?? '', i.task_ref ?? ''].join(' ').toLowerCase().includes(term);
        });
        setResults(hits.slice(0, 8));
        setSel(0);
      } catch {
        setResults([]);
      }
    }, 120);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [q, period]);

  const go = (id: string) => {
    setOpen(false);
    setQ('');
    openDrawer(id);
  };

  return (
    <div className="search" ref={ref}>
      <IconSearch size={15} />
      <input
        ref={inputRef}
        className="search-input"
        placeholder="Search work items"
        value={q}
        aria-label="Search work items"
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') setSel((s) => Math.min(results.length - 1, s + 1));
          if (e.key === 'ArrowUp') setSel((s) => Math.max(0, s - 1));
          if (e.key === 'Enter') {
            if (results[sel]) go(results[sel].id);
            else if (/^(WI|JE)-/i.test(q.trim())) navigate(q.trim().toUpperCase().startsWith('JE') ? `/ledger?je=${q.trim().toUpperCase()}` : `/items/${q.trim().toUpperCase()}`);
          }
          if (e.key === 'Escape') {
            setOpen(false);
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      <kbd className="kbd">/</kbd>
      {open && q.trim() && (
        <ul className="menu menu-search">
          {results.length === 0 && <li className="menu-empty">No work items match {q.trim()}.</li>}
          {results.map((r, i) => (
            <li key={r.id}>
              <button type="button" className={`menu-item search-hit${i === sel ? ' is-selected' : ''}`} onMouseEnter={() => setSel(i)} onClick={() => go(r.id)}>
                <span className="mono ink-3 search-id">{r.id}</span>
                <span className="truncate">{r.title}</span>
                <span className="tnum ink-2">{money(r.amount_cents)}</span>
                <Pill state={r.state} item={r} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActingAs() {
  const { token, setActing, actorName, theme, setTheme, toast, world } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false), open);
  const me = PEOPLE.find((p) => p.token === token)!;
  const openPeriod = world?.periods.find((p) => p.status === 'open')?.id;
  const { data: summary } = useApi<Summary>(openPeriod ? `/api/summary?period=${openPeriod}` : null);
  const themes: { t: Theme; label: string; icon: ReactNode }[] = [
    { t: 'system', label: 'System', icon: <IconMonitor size={14} /> },
    { t: 'light', label: 'Light', icon: <IconSun size={14} /> },
    { t: 'dark', label: 'Dark', icon: <IconMoon size={14} /> },
  ];
  return (
    <div className="menu-wrap acting" ref={ref}>
      <button type="button" className="acting-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="acting-label ink-3">Acting as</span>
        <Avatar id={me.id} size={24} />
        <span className="acting-who">
          <span className="acting-name">{actorName(me.id)}</span>
          <span className="acting-role">{me.label}</span>
        </span>
        <IconChevron size={14} />
      </button>
      {open && (
        <div className="menu menu-acting" role="menu">
          <p className="menu-head">Act as</p>
          {PEOPLE.map((p) => {
            const n = summary?.inbox_counts?.[p.id];
            return (
              <button
                key={p.token}
                type="button"
                role="menuitemradio"
                aria-checked={p.token === token}
                className={`menu-item${p.token === token ? ' is-selected' : ''}`}
                onClick={() => {
                  setActing(p.token);
                  setOpen(false);
                  if (p.token !== token) toast(`Now acting as ${actorName(p.id)}`);
                }}
              >
                <Avatar id={p.id} size={22} />
                <span className="menu-item-main">
                  <span>{actorName(p.id)}</span>
                  <span className="menu-item-sub">{p.label}</span>
                </span>
                {n ? <span className="nav-count tnum">{n}</span> : p.token === token ? <IconCheck size={14} /> : null}
              </button>
            );
          })}
          <p className="menu-note ink-3">Agents act only through the API with their own tokens, so they are not in this list.</p>
          <div className="menu-sep" />
          <p className="menu-head">Theme</p>
          <div className="seg" role="radiogroup" aria-label="Theme">
            {themes.map((x) => (
              <button key={x.t} type="button" role="radio" aria-checked={theme === x.t} className={`seg-btn${theme === x.t ? ' is-on' : ''}`} onClick={() => setTheme(x.t)}>
                {x.icon}
                {x.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Footer() {
  const { world, chain, fixtures } = useStore();
  const live = (world?.rail?.mode ?? world?.rail_mode) === 'live';
  return (
    <footer className="footer">
      <span>{world?.company.synthetic_notice ?? 'Kestrel Labs is a fictional company. All data is synthetic.'}</span>
      <span className="footer-sep" />
      <span className="footer-rail">Settlement is {live ? 'live on Base Sepolia' : 'an offline simulation'}</span>
      <span className="footer-sep" />
      <span>Times in UTC</span>
      {fixtures && (
        <>
          <span className="footer-sep" />
          <span className="footer-fixture">Fixture data</span>
        </>
      )}
      <span className="footer-chain">
        {chain ? (
          chain.ok ? (
            <span className="ok chain-ok">
              <IconChain size={14} /> Audit chain verified <span className="tnum ink-2">{chain.checked.toLocaleString('en-US')} events</span>
            </span>
          ) : (
            <span className="deny chain-bad">
              <IconWarn size={14} /> Audit chain broken at event {chain.first_bad_seq}
            </span>
          )
        ) : (
          <span className="ink-3">Checking audit chain</span>
        )}
      </span>
    </footer>
  );
}

function Shortcuts({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ['g t', 'Go to Today'],
    ['g i', 'Go to Inbox'],
    ['g a', 'Go to Activity'],
    ['g r', 'Go to Reconcile'],
    ['g l', 'Go to Ledger'],
    ['g p', 'Go to Policies'],
    ['g u', 'Go to Audit'],
    ['g s', 'Go to Scenarios'],
    ['/', 'Search work items'],
    ['j and k', 'Move through rows'],
    ['Enter', 'Open the selected row'],
    ['Esc', 'Close the drawer'],
    ['?', 'Show or hide this sheet'],
  ];
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>Keyboard shortcuts</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <dl className="keys">
          {rows.map(([k, v]) => (
            <div key={k} className="keys-row">
              <dt>
                {k.split(' ').map((p, i) => (p === 'and' ? <span key={i} className="ink-3"> and </span> : <kbd key={i} className="kbd">{p}</kbd>))}
              </dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function Toasts() {
  const { toasts } = useStore();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          {t.tone === 'ok' && <IconCheck size={14} />}
          {t.tone === 'deny' && <IconWarn size={14} />}
          {t.text}
        </div>
      ))}
    </div>
  );
}
