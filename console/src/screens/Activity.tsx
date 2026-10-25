import { useMemo, useState } from 'react';
import type { FlagCode, State, WorkItemList } from '../api/types';
import { ErrorState, FLAG_LABEL, Flags, Money, Skeleton } from '../components/Bits';
import { Pill, stateLabel } from '../components/Pill';
import { dateTime, periodLabel } from '../lib/format';
import { useListKeys } from '../lib/hooks';
import { useApi, useStore } from '../lib/store';
import { useDrawer } from '../shell/Drawer';

const STATES: State[] = ['NEEDS_APPROVAL', 'NEEDS_INFO', 'PROPOSED', 'APPROVED', 'PAID', 'POSTED', 'DENIED', 'REJECTED', 'FAILED'];
const KINDS = ['purchase', 'invoice', 'accrual', 'correction', 'policy'];
const KIND_LABEL: Record<string, string> = { purchase: 'Purchases', invoice: 'Invoices', accrual: 'Accruals', correction: 'Corrections', policy: 'Policy proposals' };

export function Activity() {
  const { period, world, actorName } = useStore();
  const { open, openId } = useDrawer();
  const initial = (k: string) => new URLSearchParams(window.location.search).get(k) ?? '';
  const [state, setState] = useState(() => initial('state'));
  const [kind, setKind] = useState(() => initial('kind'));
  const [agent, setAgent] = useState(() => initial('agent'));
  const [vendor, setVendor] = useState(() => initial('vendor'));
  const [flag, setFlag] = useState(() => initial('flag'));
  const [q, setQ] = useState('');
  const { data, error, loading } = useApi<WorkItemList>(period ? `/api/work-items?period=${period}&limit=500` : null);

  const rows = useMemo(() => {
    let r = data?.items ?? [];
    if (state) r = r.filter((i) => i.state === state);
    if (kind) r = r.filter((i) => i.kind === kind);
    if (agent) r = r.filter((i) => i.requested_by === agent);
    if (vendor) r = r.filter((i) => i.vendor_id === vendor);
    if (flag) r = r.filter((i) => i.flags.includes(flag as FlagCode));
    const t = q.trim().toLowerCase();
    if (t) r = r.filter((i) => [i.id, i.title, i.purpose ?? '', i.task_ref ?? '', i.memo ?? ''].join(' ').toLowerCase().includes(t));
    return [...r].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  }, [data, state, kind, agent, vendor, flag, q]);

  const [ki] = useListKeys(rows.length, (i) => open(rows[i].id));
  const flagsPresent = useMemo(() => [...new Set((data?.items ?? []).flatMap((i) => i.flags))], [data]);
  const anyFilter = state || kind || agent || vendor || flag || q;
  const decidedBy = (approvedBy: string | null | undefined, st: State) => {
    if (approvedBy === 'system:policy') return <span className="decided-elix">Elix policy</span>;
    if (approvedBy) return actorName(approvedBy);
    if (st === 'DENIED') return <span className="decided-elix">Elix policy</span>;
    return <span className="ink-3">Waiting</span>;
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Activity</h1>
          <p className="page-sub">Every work item in {period ? periodLabel(period) : 'this period'}, newest first.</p>
        </div>
        <p className="page-meta tnum ink-2">{data ? `${rows.length.toLocaleString('en-US')} of ${data.total.toLocaleString('en-US')} items` : ''}</p>
      </header>
      <div className="filters">
        <input className="input input-search" placeholder="Filter by id, title, purpose or task" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter" />
        <select className="input" value={state} onChange={(e) => setState(e.target.value)} aria-label="State">
          <option value="">Any state</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {stateLabel(s)}
            </option>
          ))}
        </select>
        <select className="input" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">Any kind</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        <select className="input" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
          <option value="">Any agent</option>
          {world?.agents.map((a) => (
            <option key={a.actor_id} value={a.actor_id}>
              {a.name}
            </option>
          ))}
        </select>
        <select className="input" value={vendor} onChange={(e) => setVendor(e.target.value)} aria-label="Vendor">
          <option value="">Any vendor</option>
          {world?.vendors.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <select className="input" value={flag} onChange={(e) => setFlag(e.target.value)} aria-label="Flag">
          <option value="">Any flag</option>
          {flagsPresent.map((f) => (
            <option key={f} value={f}>
              {FLAG_LABEL[f]?.[0]} {FLAG_LABEL[f]?.[1]}
            </option>
          ))}
        </select>
        {anyFilter && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setState('');
              setKind('');
              setAgent('');
              setVendor('');
              setFlag('');
              setQ('');
            }}
          >
            Clear filters
          </button>
        )}
      </div>
      <ErrorState error={error} what="activity" />
      <div className="table-wrap">
        <table className="table table-dense activity-table">
          <colgroup>
            <col style={{ width: 76 }} />
            <col style={{ width: 178 }} />
            <col />
            <col style={{ width: 66 }} />
            <col style={{ width: 136 }} />
            <col style={{ width: 100 }} />
            <col style={{ width: 66 }} />
            <col style={{ width: 150 }} />
            <col style={{ width: 112 }} />
            <col style={{ width: 112 }} />
          </colgroup>
          <thead>
            <tr>
              <th>Id</th>
              <th>State</th>
              <th>Title</th>
              <th>Agent</th>
              <th>Vendor</th>
              <th className="num">Amount</th>
              <th>Account</th>
              <th>Flags</th>
              <th>Decided by</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {loading && !data ? (
              <tr>
                <td colSpan={10}>
                  <Skeleton rows={12} cols={6} />
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={10} className="table-empty">
                  No work items match these filters.
                </td>
              </tr>
            ) : (
              rows.map((it, i) => {
                const ag = world?.agents.find((a) => a.actor_id === it.requested_by);
                const v = world?.vendors.find((x) => x.id === it.vendor_id);
                return (
                  <tr key={it.id} className={`row-click${ki === i ? ' is-cursor' : ''}${openId === it.id ? ' is-selected' : ''}`} onClick={() => open(it.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && open(it.id)}>
                    <td className="mono ink-2">{it.id}</td>
                    <td>
                      <Pill state={it.state} item={it} />
                    </td>
                    <td className="truncate cell-title">{it.title}</td>
                    <td>{ag?.name ?? <span className="ink-3">{it.kind === 'invoice' ? 'Sync' : ''}</span>}</td>
                    <td className="truncate">{v?.name ?? ''}</td>
                    <td className="num">
                      <Money cents={it.amount_cents || null} />
                    </td>
                    <td className="tnum ink-2">{it.account ?? ''}</td>
                    <td className="cell-flags">
                      <Flags flags={it.flags} max={1} />
                    </td>
                    <td className="truncate">{decidedBy(it.approved_by, it.state)}</td>
                    <td className="tnum ink-2 nowrap">{dateTime(it.updated_at)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
