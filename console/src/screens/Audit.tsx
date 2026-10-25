import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { AuditEvent, ChainCheck, WhyReport } from '../api/types';
import { AuditRow } from '../components/AuditRow';
import { Avatar, ErrorState, InlineError, ItemLink, Skeleton } from '../components/Bits';
import { IconChain, IconCheck, IconWarn } from '../components/Icons';
import { date, dateTime, money, time } from '../lib/format';
import { useApi, useStore } from '../lib/store';
import { useDrawer } from '../shell/Drawer';

const PRESETS: { label: string; subject: string }[] = [
  { label: 'Why was LUM-2026-10A paid short?', subject: 'LUM-2026-10A' },
  { label: 'Why is the Stormvault archive on hold?', subject: 'stormvault-archive' },
];

export function Audit() {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Audit</h1>
          <p className="page-sub">Every attempt Elix saw, allowed or refused, in one hash chained log. Ask why anything is in the state it is in.</p>
        </div>
      </header>
      <WhyBox />
      <EventLog />
    </div>
  );
}

function WhyBox() {
  const [q, setQ] = useState(() => new URLSearchParams(window.location.search).get('why') ?? '');
  const [report, setReport] = useState<WhyReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { open } = useDrawer();

  const resolve = async (subject: string): Promise<string> => {
    if (subject !== 'stormvault-archive') return subject;
    const r = await api<{ items: { id: string; sku?: string | null; state: string }[] }>('/api/work-items?vendor=ven_stormvault&limit=200');
    const hit = r.items.find((i) => i.sku === 'archive-tx-2025' && i.state === 'NEEDS_APPROVAL') ?? r.items.find((i) => i.sku === 'archive-tx-2025');
    if (!hit) throw new ApiError(404, 'not_found', 'There is no Stormvault archive request yet. Run the payee change scenario first.');
    return hit.id;
  };

  const ask = async (subject: string, label?: string) => {
    if (!subject.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const id = await resolve(subject.trim());
      setQ(label ? id : subject.trim());
      const r = await api<WhyReport>(`/api/why/${encodeURIComponent(id)}`);
      setReport(r);
    } catch (e) {
      setReport(null);
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const w = new URLSearchParams(window.location.search).get('why');
    if (w) ask(w);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section className="why panel">
      <form
        className="why-form"
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
      >
        <label className="why-label" htmlFor="why-input">
          Ask why
        </label>
        <div className="why-row">
          <input id="why-input" className="input why-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Work item, journal entry, invoice number or policy id" />
          <button type="submit" className="btn btn-primary" disabled={busy || !q.trim()}>
            {busy ? 'Asking' : 'Get the answer'}
          </button>
        </div>
        <div className="why-presets">
          {PRESETS.map((p) => (
            <button key={p.subject} type="button" className="btn btn-secondary btn-sm" onClick={() => ask(p.subject, p.label)}>
              {p.label}
            </button>
          ))}
        </div>
      </form>
      <InlineError message={err} />
      {report && (
        <div className="why-report">
          <div className="why-answer">
            <h2 className="why-title">{report.title}</h2>
            <p className="why-text">{report.answer}</p>
            {report.related.length > 0 && (
              <p className="why-related">
                <span className="ink-3">Related</span>
                {report.related.slice(0, 8).map((r) =>
                  /^(WI|JE)-|^pol-/.test(r) ? <ItemLink key={r} id={r} /> : <span key={r} className="mono ink-2">{r}</span>,
                )}
              </p>
            )}
          </div>
          <ol className="why-steps">
            {report.steps.map((s, i) => (
              <li key={s.seq} className={`why-step${s.action.startsWith('denied') ? ' why-step-denied' : ''}`}>
                <span className="why-step-n tnum">{i + 1}</span>
                <div className="why-step-body">
                  <p className="why-step-head">
                    <Avatar id={s.actor} size={18} />
                    <span className="why-actor">{s.actor_name}</span>
                    <span className="ink-3 tnum">
                      {date(s.at)}, {time(s.at)}
                    </span>
                    <span className="mono ink-3 why-seq">event {s.seq.toLocaleString('en-US')}</span>
                  </p>
                  <p className="why-sentence">{s.sentence}</p>
                  {(s.rule_ids.length > 0 || Object.keys(s.evidence ?? {}).length > 0) && (
                    <p className="why-evidence">
                      {s.rule_ids.map((r) => (
                        <span key={r} className="chip">
                          <span className="chip-rule mono">{r}</span>
                        </span>
                      ))}
                      {evidenceBits(s.evidence).map(([k, v]) => (
                        <span key={k} className="why-ev">
                          <span className="ink-3">{k.replace(/_/g, ' ')}</span> <span className="mono" onClick={() => /^WI-/.test(v) && open(v)}>{v}</span>
                        </span>
                      ))}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

const SKIP_EVIDENCE = new Set(['rule_ids', 'rule_id', 'rules', 'failed_rules', 'triggered_by']);

function evidenceBits(ev: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(ev ?? {})) {
    if (SKIP_EVIDENCE.has(k) || v == null || v === false || v === '') continue;
    let s: string;
    if (Array.isArray(v)) {
      if (!v.length || v.some((x) => typeof x === 'object')) continue;
      s = v.join(', ');
    } else if (typeof v === 'object') {
      continue;
    } else if (typeof v === 'number' && /cents$/.test(k)) {
      s = money(v);
    } else {
      s = String(v);
    }
    const label = k.replace(/_cents$/, '').replace(/_/g, ' ');
    out.push([label, s.length > 48 ? s.slice(0, 20) + '…' + s.slice(-8) : s]);
    if (out.length >= 4) break;
  }
  return out;
}

function EventLog() {
  const { world, refreshChain, chain } = useStore();
  const { open } = useDrawer();
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [denied, setDenied] = useState(false);
  const [limit, setLimit] = useState(100);
  const params = new URLSearchParams();
  params.set('limit', String(limit));
  if (actor) params.set('actor', actor);
  if (action) params.set('action', action);
  if (denied) params.set('allowed', 'false');
  const { data, error, loading } = useApi<{ events: AuditEvent[] }>(`/api/audit?${params.toString()}`);
  const [verifying, setVerifying] = useState(false);
  const [verified, setVerified] = useState<ChainCheck | null>(null);
  const verify = async () => {
    setVerifying(true);
    const c = await refreshChain();
    setVerified(c);
    setVerifying(false);
  };
  const c = verified ?? chain;
  const actions = ['propose', 'evaluate', 'auto_approve', 'escalate', 'approve', 'reject', 'verify_payee', 'pay', 'post', 'deny', 'recode', 'learn', 'close'];

  return (
    <section className="section">
      <div className="section-head">
        <h2 className="section-title">Event log</h2>
        <div className="chain-box">
          {c && (
            <span className={c.ok ? 'ok chain-state' : 'deny chain-state'}>
              {c.ok ? <IconCheck size={14} /> : <IconWarn size={14} />}
              {c.ok ? `Chain verified across ${c.checked.toLocaleString('en-US')} events` : `Chain broken at event ${c.first_bad_seq}`}
              {c.head_hash && <span className="mono ink-3"> head {c.head_hash.slice(0, 12)}</span>}
            </span>
          )}
          <button type="button" className="btn btn-secondary btn-sm" onClick={verify} disabled={verifying}>
            <IconChain size={14} />
            {verifying ? 'Verifying' : 'Verify chain'}
          </button>
        </div>
      </div>
      {verified && <p className="notice tnum">Recomputed every hash from event 1 to {verified.checked.toLocaleString('en-US')} at {dateTime(new Date().toISOString())}. {verified.ok ? 'Nothing has been changed or removed.' : `Event ${verified.first_bad_seq} does not match its hash.`}</p>}
      <div className="filters">
        <select className="input" value={actor} onChange={(e) => setActor(e.target.value)} aria-label="Actor">
          <option value="">Any actor</option>
          {world?.actors.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <select className="input" value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
          <option value="">Any action</option>
          {actions.map((a) => (
            <option key={a} value={a}>
              {a.replace('_', ' ')}
            </option>
          ))}
        </select>
        <label className="check-toggle">
          <input type="checkbox" checked={denied} onChange={(e) => setDenied(e.target.checked)} />
          <span>Denied attempts only</span>
        </label>
      </div>
      <ErrorState error={error} what="audit events" />
      {loading && !data ? (
        <Skeleton rows={12} cols={4} />
      ) : data?.events.length === 0 ? (
        <p className="ink-3 table-empty">No events match these filters.</p>
      ) : (
        <ol className="audit-list audit-list-log panel">
          {data?.events.map((e) => (
            <AuditRow key={e.seq} e={e} showDate onOpenSubject={open} />
          ))}
        </ol>
      )}
      {data && data.events.length >= limit && (
        <button type="button" className="btn btn-ghost list-more" onClick={() => setLimit((l) => l + 200)}>
          Show older events
        </button>
      )}
    </section>
  );
}
