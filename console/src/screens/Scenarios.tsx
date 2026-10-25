import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { Scenario, ScenarioResult } from '../api/types';
import { ErrorState, InlineError, ItemLink, Skeleton } from '../components/Bits';
import { dateTime, money } from '../lib/format';
import { PEOPLE, useApi, useStore } from '../lib/store';

/** Who acts next after each scenario, so the presenter can switch with one click. */
const NEXT: Record<string, { token: string; to: string; what: string }[]> = {
  'cross-rail-double-bill': [{ token: 'tok_sam', to: '/reconcile', what: 'approves the short pay' }],
  'vendor-credit': [{ token: 'tok_sam', to: '/reconcile', what: 'sees the dispute closed' }],
  'payee-change': [
    { token: 'tok_dev', to: '/inbox', what: 'approves the spend' },
    { token: 'tok_priya', to: '/inbox', what: 'verifies the payee' },
  ],
  'retry-double-pay': [{ token: 'tok_dev', to: '/inbox', what: 'sees only one request' }],
  'self-approval': [{ token: 'tok_maya', to: '/inbox', what: 'decides the order' }],
  'split-purchase': [{ token: 'tok_maya', to: '/inbox', what: 'reviews the split' }],
  'closed-period-fix': [{ token: 'tok_priya', to: '/inbox', what: 'approves the correction and policy' }],
  'learn-policies': [{ token: 'tok_priya', to: '/policies', what: 'approves the proposals' }],
  'month-end-accruals': [{ token: 'tok_priya', to: '/inbox', what: 'approves the accruals' }],
  'audit-question': [{ token: 'tok_jordan', to: '/audit?why=LUM-2026-10A', what: 'reads the answer' }],
  custom: [],
};

export function Scenarios() {
  const { data, error, loading, reload } = useApi<{ scenarios: Scenario[] }>('/api/scenarios');
  const list = data?.scenarios ?? [];
  const story = list.filter((s) => s.id !== 'custom');
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Scenarios</h1>
          <p className="page-sub">The presenter's controls. Each scenario acts as the right agent or system and stops where a person has to decide.</p>
        </div>
        <ResetDemo onDone={reload} />
      </header>
      <ErrorState error={error} what="scenarios" />
      {loading && !data ? (
        <Skeleton rows={8} cols={3} />
      ) : (
        <div className="scen-grid">
          <ol className="scen-list">
            {story.map((s, i) => (
              <ScenarioRow key={s.id} s={s} n={i + 1} onRan={reload} />
            ))}
          </ol>
          <CustomScenario />
        </div>
      )}
    </div>
  );
}

function ScenarioRow({ s, n, onRan }: { s: Scenario; n: number; onRan: () => void }) {
  const { setActing, actorName, toast, refresh } = useStore();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ScenarioResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<ScenarioResult>(`/api/scenarios/${s.id}/run`, { method: 'POST', body: {} });
      setRes(r);
      toast(`${s.title} is ready.`, 'ok');
      onRan();
      refresh();
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  const ids = res?.result_ids ?? s.last_run?.result_ids ?? [];
  const mapped = NEXT[s.id] ?? [];
  const nextActor = (res as (ScenarioResult & { next_actor?: string | null }) | null)?.next_actor ?? s.next_actor ?? null;
  const fromApi = nextActor && !mapped.some((m) => PEOPLE.find((p) => p.token === m.token)?.id === nextActor) ? PEOPLE.filter((p) => p.id === nextActor).map((p) => ({ token: p.token, to: '/inbox', what: 'decides next' })) : [];
  const next = [...mapped, ...fromApi];
  return (
    <li className={`scen${res ? ' scen-ran' : ''}`}>
      <span className="scen-n tnum">{n}</span>
      <div className="scen-body">
        <div className="scen-head">
          <h2 className="scen-title">{s.title}</h2>
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy || !s.ready} onClick={run}>
            {busy ? 'Running' : 'Run scenario'}
          </button>
        </div>
        <p className="scen-desc ink-2">{s.description}</p>
        {!s.ready && s.not_ready_reason && !res && <p className={s.last_run ? 'scen-wait-done ink-3' : 'scen-wait'}>{s.not_ready_reason}</p>}
        {s.prerequisites.length > 0 && (
          <p className="scen-pre ink-3">
            Needs
            {s.prerequisites.map((p) => (
              <span key={p} className="chip">
                {p}
              </span>
            ))}
          </p>
        )}
        {(res || s.last_run) && (
          <div className="scen-result">
            {res?.message && <p>{res.message}</p>}
            <div className="scen-result-row">
              {!res && s.last_run && <span className="ink-2 tnum">Last run {dateTime(s.last_run.at)}</span>}
              {ids.length > 0 && (
                <span className="scen-ids">
                  {ids.map((id) => (
                    <ItemLink key={id} id={id} />
                  ))}
                </span>
              )}
            </div>
            {next.length > 0 && (
              <p className="scen-next">
                {next.map((x) => {
                  const p = PEOPLE.find((pp) => pp.token === x.token)!;
                  return (
                    <button
                      key={x.token}
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        setActing(x.token);
                        navigate(x.to);
                      }}
                    >
                      Open {x.to.startsWith('/inbox') ? 'inbox' : x.to.startsWith('/audit') ? 'audit' : x.to.slice(1)} as {actorName(p.id).split(' ')[0]}
                    </button>
                  );
                })}
                <span className="ink-3">{next.map((x) => `${actorName(PEOPLE.find((p) => p.token === x.token)!.id).split(' ')[0]} ${x.what}`).join(', then ')}.</span>
              </p>
            )}
          </div>
        )}
        <InlineError message={err} />
      </div>
    </li>
  );
}

function CustomScenario() {
  const { world, toast, refresh } = useStore();
  const agents = world?.agents ?? [];
  const vendors = (world?.vendors ?? []).filter((v) => v.rails.includes('x402'));
  const [agent, setAgent] = useState('agent:scout');
  const [vendor, setVendor] = useState('ven_parsec');
  const v = vendors.find((x) => x.id === vendor);
  const [sku, setSku] = useState('query-1k');
  const [quantity, setQuantity] = useState('2');
  const [price, setPrice] = useState('');
  const [payTo, setPayTo] = useState('');
  const [memo, setMemo] = useState('');
  const [purpose, setPurpose] = useState('Search queries to find carrier underwriting contacts');
  const [taskRef, setTaskRef] = useState('GTM-318');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<ScenarioResult | null>(null);
  const skuObj = v?.skus.find((s) => s.sku === sku) ?? v?.skus[0];
  const unit = price ? Math.round(parseFloat(price) * 100) : skuObj?.unit_price_cents ?? 0;
  const amount = useMemo(() => Math.round(unit * (parseFloat(quantity) || 0)), [unit, quantity]);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    setRes(null);
    try {
      const body = {
        agent_id: agent,
        vendor_id: vendor,
        sku: skuObj?.sku ?? sku,
        quantity: parseFloat(quantity) || 0,
        unit_price_cents: price ? Math.round(parseFloat(price) * 100) : null,
        pay_to_override: payTo.trim() || null,
        memo: memo.trim() || null,
        purpose,
        task_ref: taskRef.trim() || null,
      };
      const r = await api<ScenarioResult>('/api/scenarios/custom/run', { method: 'POST', body });
      setRes(r);
      refresh();
      toast('Purchase submitted.', 'ok');
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="custom panel">
      <h2 className="section-title">Try your own purchase</h2>
      <p className="ink-2 custom-lede">Submit any request as any agent and watch the checks decide. Elix treats it exactly like a real one.</p>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">Agent</span>
          <select className="input" value={agent} onChange={(e) => setAgent(e.target.value)}>
            {agents.map((a) => (
              <option key={a.actor_id} value={a.actor_id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Vendor</span>
          <select
            className="input"
            value={vendor}
            onChange={(e) => {
              setVendor(e.target.value);
              const nv = vendors.find((x) => x.id === e.target.value);
              setSku(nv?.skus[0]?.sku ?? '');
            }}
          >
            {vendors.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Item</span>
          <select className="input" value={skuObj?.sku ?? ''} onChange={(e) => setSku(e.target.value)}>
            {v?.skus.map((s) => (
              <option key={s.sku} value={s.sku}>
                {s.description}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Quantity</span>
          <input className="input tnum" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Unit price override</span>
          <input className="input tnum" inputMode="decimal" placeholder={skuObj ? (skuObj.unit_price_cents / 100).toFixed(2) : ''} value={price} onChange={(e) => setPrice(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Pay to override</span>
          <input className="input mono" placeholder="Optional" value={payTo} onChange={(e) => setPayTo(e.target.value)} />
        </label>
        <label className="field field-span">
          <span className="field-label">Purpose</span>
          <input className="input" value={purpose} onChange={(e) => setPurpose(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Task reference</span>
          <input className="input mono" value={taskRef} onChange={(e) => setTaskRef(e.target.value)} />
        </label>
        <label className="field field-span">
          <span className="field-label">Memo</span>
          <input className="input" placeholder="Try pasting a note that claims the order is pre-approved" value={memo} onChange={(e) => setMemo(e.target.value)} />
        </label>
      </div>
      <div className="custom-foot">
        <span className="custom-amt tnum">
          <span className="fig-label">Request total</span> {money(amount)}
        </span>
        <button type="button" className="btn btn-primary" disabled={busy || !purpose} onClick={submit}>
          {busy ? 'Submitting' : 'Submit purchase'}
        </button>
      </div>
      <InlineError message={err} />
      {res && (
        <div className="scen-result">
          <p>{res.message}</p>
          <p className="scen-ids">
            {res.result_ids.map((id) => (
              <ItemLink key={id} id={id} />
            ))}
          </p>
        </div>
      )}
    </aside>
  );
}

function ResetDemo({ onDone }: { onDone: () => void }) {
  const { toast, reloadWorld } = useStore();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const reset = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api('/api/demo/reset', { method: 'POST' });
      toast('Demo data reset.', 'ok');
      setConfirming(false);
      reloadWorld();
      onDone();
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="reset">
      {confirming ? (
        <div className="reset-confirm">
          <span className="ink-2">This reseeds Kestrel Labs and clears every scenario run.</span>
          <button type="button" className="btn btn-danger-solid" disabled={busy} onClick={reset}>
            {busy ? 'Resetting' : 'Reset demo data'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
            Keep data
          </button>
        </div>
      ) : (
        <button type="button" className="btn btn-secondary" onClick={() => setConfirming(true)}>
          Reset demo data
        </button>
      )}
      <InlineError message={err} />
    </div>
  );
}
