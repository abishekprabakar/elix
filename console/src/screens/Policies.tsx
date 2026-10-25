import { useState } from 'react';
import { api, ApiError } from '../api/client';
import type { Policy, World } from '../api/types';
import { Avatar, ErrorState, InlineError, ItemLink, Skeleton } from '../components/Bits';
import { IconLock } from '../components/Icons';
import { Pill } from '../components/Pill';
import { calDate, dateYear, moneyWhole, plural } from '../lib/format';
import { useApi, useStore } from '../lib/store';

const SKU_PHRASE: Record<string, string> = {
  'scene-10m': '10 m scenes',
  'scene-3m': '3 m scenes',
  'station-day': 'radar station days',
  'archive-tx-2025': 'the Texas radar archive',
  'h100-spot-hr': 'H100 spot hours',
  'h100-ondemand-hr': 'H100 on demand hours',
  'firmo-record': 'firmographic records',
  'query-1k': 'query packs',
};

export function policySentence(p: Policy, w: World | null): string {
  const agent = w?.agents.find((a) => a.actor_id === p.scope.agent_id)?.name ?? 'any agent';
  const vendor = w?.vendors.find((v) => v.id === p.scope.vendor_id);
  const vname = vendor ? vendor.name.split(' ')[0] : 'any vendor';
  const what = p.scope.sku ? SKU_PHRASE[p.scope.sku as string] ?? p.scope.sku : 'purchases';
  if (p.kind === 'auto_approve') return `Auto approve ${agent} buying ${vname} ${what}${p.params.max_amount_cents != null ? ` up to ${moneyWhole(p.params.max_amount_cents)}` : ''}`;
  if (p.kind === 'coding') {
    const acct = w?.accounts.find((a) => a.code === p.params.account);
    return `Book ${agent}'s ${vname} purchases to ${p.params.account} ${acct?.name ?? ''}`.trim();
  }
  return `Allow up to ${p.params.tolerance_pct ?? 10} percent over contract prices`;
}

function windowWords(w?: string): string {
  if (!w) return 'the history window';
  const m = w.match(/^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})$/);
  if (m) return `${calDate(m[1])} to ${calDate(m[2])}`;
  return w;
}

function evidenceSentence(p: Policy, w: World | null, name: (id?: string | null) => string): string {
  const e = p.evidence;
  const rejected = e.rejections ? plural(e.rejections, 'rejection') : 'none rejected';
  if (p.kind === 'coding') {
    const from = (e.from_account as string | undefined) ?? '';
    return `${plural(e.precedent_count ?? 0, 'recode')}${from ? ` from ${from}` : ''} to ${p.params.account}, ${e.rejections ? rejected : 'none in the other direction or rejected'}.`;
  }
  if (!e.precedent_count) return e.window ? `${windowWords(e.window)}.` : '';
  const agent = w?.agents.find((a) => a.actor_id === p.scope.agent_id);
  const ids = (e.approvers as string[] | undefined) ?? ((e.approver as string | undefined) ? [e.approver as string] : agent ? [agent.owner_id] : []);
  const who = ids.map((i) => name(i)).join(' and ') || 'people';
  const largest = e.max_seen_cents ? `, largest ${moneyWhole(e.max_seen_cents)}` : '';
  return `${plural(e.precedent_count, 'approval')} by ${who} from ${windowWords(e.window)}, ${rejected}${largest}.`;
}

export function Policies() {
  const { world, session, actorName, toast, refresh } = useStore();
  const { data, error, loading, reload } = useApi<{ policies: Policy[] }>('/api/policies');
  const [busy, setBusy] = useState(false);
  const [learned, setLearned] = useState<string | null>(null);
  const [showOld, setShowOld] = useState(false);
  const all = data?.policies ?? [];
  const proposed = all.filter((p) => p.status === 'proposed');
  const active = all.filter((p) => p.status === 'active');
  const old = all.filter((p) => p.status === 'rejected' || p.status === 'retired');
  const canApprove = !!session?.can.approve_policies;
  const isHuman = session?.actor.kind === 'human';

  const learn = async () => {
    setBusy(true);
    setLearned(null);
    try {
      const r = await api<{ proposed: Policy[] }>('/api/policies/learn', { method: 'POST' });
      const n = r.proposed.length;
      setLearned(n ? `The learner proposed ${plural(n, 'new policy', 'new policies')}. ${r.proposed.map((p) => p.id).join(', ')} ${n === 1 ? 'needs' : 'need'} the controller.` : 'The learner found nothing new. Every group that qualifies already has a proposal or an active policy.');
      reload();
    } catch (e) {
      setLearned((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Policies</h1>
          <p className="page-sub">What Elix may approve or code on its own. The learner proposes from history and corrections, and only the controller can turn a proposal on.</p>
        </div>
        <button type="button" className="btn btn-secondary" disabled={!isHuman || busy} onClick={learn}>
          {busy ? 'Learning' : 'Learn from history'}
        </button>
      </header>
      {learned && <p className="notice">{learned}</p>}
      <ErrorState error={error} what="policies" />
      {loading && !data ? (
        <Skeleton rows={6} cols={4} />
      ) : (
        <>
          <section className="section">
            <div className="section-head">
              <h2 className="section-title">
                Proposed <span className="count tnum">{proposed.length}</span>
              </h2>
              {!canApprove && proposed.length > 0 && (
                <span className="action-reason">
                  <IconLock size={12} /> Learned policies need the controller. Elix will refuse and log an approval from anyone else.
                </span>
              )}
            </div>
            {proposed.length === 0 ? (
              <p className="ink-3">No proposals waiting. Run Learn from history to look again.</p>
            ) : (
              <ul className="policy-list">
                {proposed.map((p) => (
                  <PolicyRow key={p.id} p={p} world={world} name={actorName} canApprove={!!isHuman} onDone={(msg) => { toast(msg, 'ok'); reload(); refresh(); }} />
                ))}
              </ul>
            )}
          </section>
          <section className="section">
            <div className="section-head">
              <h2 className="section-title">
                Active <span className="count tnum">{active.length}</span>
              </h2>
            </div>
            <ul className="policy-list">
              {active.map((p) => (
                <PolicyRow key={p.id} p={p} world={world} name={actorName} canApprove={false} onDone={() => reload()} />
              ))}
            </ul>
          </section>
          {old.length > 0 && (
            <section className="section">
              <button type="button" className="btn btn-ghost" aria-expanded={showOld} onClick={() => setShowOld((s) => !s)}>
                {showOld ? 'Hide' : 'Show'} {plural(old.length, 'rejected or retired policy', 'rejected or retired policies')}
              </button>
              {showOld && (
                <ul className="policy-list policy-list-old">
                  {old.map((p) => (
                    <PolicyRow key={p.id} p={p} world={world} name={actorName} canApprove={false} onDone={() => reload()} />
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Bound({ p, world }: { p: Policy; world: World | null }) {
  const agent = world?.agents.find((a) => a.actor_id === p.scope.agent_id);
  const cap = agent?.per_txn_cap_cents;
  const max = p.params.max_amount_cents;
  if (!cap || max == null) return null;
  const seen = p.evidence.max_seen_cents ?? 0;
  const pos = (c: number) => `${Math.min(100, (c / cap) * 100)}%`;
  return (
    <div className="bound" aria-label={`Limit ${moneyWhole(max)} against a ${moneyWhole(cap)} cap`}>
      <div className="bound-track">
        <span className="bound-fill" style={{ width: pos(max) }} />
        {seen > 0 && <span className="bound-seen" style={{ left: pos(seen) }} title={`Largest precedent ${moneyWhole(seen)}`} />}
      </div>
      <div className="bound-labels tnum">
        <span>Limit {moneyWhole(max)}</span>
        <span className="ink-3">Cap {moneyWhole(cap)}</span>
      </div>
    </div>
  );
}

function PolicyRow({ p, world, name, canApprove, onDone }: { p: Policy; world: World | null; name: (id?: string | null) => string; canApprove: boolean; onDone: (msg: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [decided, setDecided] = useState<string | null>(null);
  const act = async (a: 'approve' | 'reject') => {
    if (decided) return;
    setBusy(a);
    setErr(null);
    try {
      await api<Policy>(`/api/policies/${p.id}/${a}`, { method: 'POST', body: { note: '' } });
      setDecided(a);
      onDone(a === 'approve' ? `Policy ${p.id} approved.` : `Policy ${p.id} rejected.`);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.auditSeq ? `${ae.message} Refused by the API and logged as event ${ae.auditSeq.toLocaleString('en-US')}.` : ae.message);
    } finally {
      setBusy(null);
    }
  };
  const statusPill = p.status === 'proposed' ? <Pill kind="needs" label="Needs the controller" /> : p.status === 'active' ? <Pill kind="active" label="Active" /> : p.status === 'rejected' ? <Pill kind="rejected" /> : <Pill kind="retired" />;
  return (
    <li className={`policy policy-${p.status}`} id={p.id}>
      <div className="policy-main">
        <div className="policy-title-row">
          <h3 className="policy-title">{policySentence(p, world)}</h3>
          {statusPill}
        </div>
        <p className="policy-evidence">{evidenceSentence(p, world, name)}</p>
        {(p.evidence.sample_ids?.length ?? 0) > 0 && (
          <p className="policy-samples">
            <span className="ink-3">{p.kind === 'coding' ? 'Recodes' : 'Precedents'}</span>
            {p.evidence.sample_ids!.map((id) => (
              <ItemLink key={id} id={id} />
            ))}
          </p>
        )}
        <dl className="policy-meta">
          <div>
            <dt>Id</dt>
            <dd className="mono">
              {p.id} v{p.version}
            </dd>
          </div>
          <div>
            <dt>Proposed by</dt>
            <dd>
              <Avatar id={p.proposed_by} size={16} /> {name(p.proposed_by)}, {dateYear(p.created_at)}
            </dd>
          </div>
          <div>
            <dt>Approved by</dt>
            <dd>{p.approved_by ? `${name(p.approved_by)}, ${dateYear(p.activated_at)}` : <span className="ink-3">Not yet</span>}</dd>
          </div>
          {p.work_item_id && (
            <div>
              <dt>Work item</dt>
              <dd>
                <ItemLink id={p.work_item_id} />
              </dd>
            </div>
          )}
        </dl>
      </div>
      <div className="policy-side">
        {p.kind === 'auto_approve' && <Bound p={p} world={world} />}
        {p.status === 'proposed' && decided && <p className="ink-2 tnum">{decided === 'approve' ? 'Approved. Updating the list.' : 'Rejected. Updating the list.'}</p>}
        {p.status === 'proposed' && !decided && (
          <div className="policy-actions">
            <button type="button" className="btn btn-primary" disabled={!canApprove || !!busy} onClick={() => act('approve')}>
              {busy === 'approve' ? 'Approving' : 'Approve policy'}
            </button>
            <button type="button" className="btn btn-danger" disabled={!canApprove || !!busy} onClick={() => act('reject')}>
              Reject
            </button>
          </div>
        )}
        <InlineError message={err} />
      </div>
    </li>
  );
}
