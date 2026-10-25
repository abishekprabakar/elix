import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { subscribe } from '../api/events';
import type { EvalCategoryRow, EvalReport } from '../api/types';
import { ErrorState, Skeleton } from '../components/Bits';
import { IconCheck, IconWarn } from '../components/Icons';
import { dateTime, pct } from '../lib/format';
import { useApi, useStore } from '../lib/store';
import { policySentence } from './Policies';

const CAT_LABEL: Record<string, string> = {
  DUPLICATE_RETRY: 'Duplicate retry with a new key',
  PAYEE_CHANGED: 'Vendor payout address changed',
  PRICE_ABOVE_CONTRACT: 'Price above contract',
  AMOUNT_MISMATCH: '402 amount differs from the request',
  OVER_TXN_CAP: 'Over the per purchase cap',
  BUDGET_EXCEEDED: 'Over the monthly budget',
  SPLIT_PURCHASE: 'Order split to stay under the cap',
  UNKNOWN_VENDOR: 'Vendor not in the master',
  MISSING_PURPOSE: 'No purpose or task',
  MEMO_INSTRUCTIONS: 'Approval claim in the memo',
  SELF_APPROVAL: 'Agent approves its own request',
  INVOICE_DOUBLE_BILL: 'Invoice bills hours paid over x402',
  INVOICE_OVERBILL: 'Invoice quantity over the meter',
  INVOICE_RATE: 'Invoice rate differs from contract',
};

function rows(r: EvalReport): EvalCategoryRow[] {
  if (Array.isArray(r.per_category)) return r.per_category;
  return Object.entries(r.per_category ?? {}).map(([category, v]) => ({ ...(v as Omit<EvalCategoryRow, 'category'>), category }) as EvalCategoryRow);
}

export function Proof() {
  const { data, error, loading, reload } = useApi<EvalReport>('/api/eval/latest');
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(
    () =>
      subscribe((e) => {
        if (e.kind !== 'eval') return;
        const d = e.data as { status?: string; progress?: number | string; message?: string };
        if (d.status === 'done' || d.status === 'finished' || d.status === 'complete') {
          setRunning(false);
          setProgress(null);
          reload();
        } else if (d.status === 'error' || d.status === 'failed') {
          setRunning(false);
          setErr(d.message ?? 'The eval stopped with an error.');
        } else {
          setProgress(typeof d.progress === 'number' ? `${Math.round(d.progress * (d.progress <= 1 ? 100 : 1))} percent` : d.message ?? String(d.progress ?? 'Running'));
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const run = async () => {
    setRunning(true);
    setErr(null);
    setProgress('Starting');
    try {
      await api('/api/eval/run', { method: 'POST' });
    } catch (e) {
      setRunning(false);
      setErr((e as ApiError).message);
    }
  };

  const missing = error && error.status === 404;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Proof</h1>
          <p className="page-sub">
            90 days of synthetic purchases and invoices with planted errors and an answer key the engine never sees. Elix is scored on what it paid without a person.
          </p>
        </div>
        <div className="head-actions">
          {progress && <span className="ink-2 tnum">{progress}</span>}
          <button type="button" className="btn btn-secondary" onClick={run} disabled={running}>
            {running ? 'Running the eval' : 'Run the eval again'}
          </button>
        </div>
      </header>
      {err && <p className="inline-error">{err}</p>}
      {missing ? (
        <div className="panel empty-panel">
          <p className="empty-title">No eval report yet.</p>
          <p className="ink-2">
            Run <code className="mono">python -m elix eval</code> from the repository root, or use the button above. The numbers on this page come only from that report.
          </p>
        </div>
      ) : error ? (
        <ErrorState error={error} what="the eval report" />
      ) : loading || !data ? (
        <Skeleton rows={8} cols={5} />
      ) : (
        <Report r={data} />
      )}
    </div>
  );
}

function Report({ r }: { r: EvalReport }) {
  const { world } = useStore();
  const cats = rows(r);
  const allCaught = cats.every((c) => c.caught === c.planted);
  const before = r.automation_rate_before_learning;
  const after = r.automation_rate_after_learning;
  const warm = r.world?.warmup_days ?? 30;
  const days = r.world?.days ?? 90;
  const learned = (r.policies_learned ?? []) as EvalReport['policies_learned'];
  return (
    <>
      <section className="proof-head">
        <div className={`proof-hero${r.anomalies_auto_paid === 0 ? '' : ' proof-hero-bad'}`}>
          <span className="fig-label">Planted errors paid without a person</span>
          <span className="proof-hero-v tnum">
            {r.anomalies_auto_paid}
            <span className="proof-hero-of"> of {r.anomalies_total}</span>
          </span>
          <span className="proof-hero-sub ink-2">
            {r.anomalies_caught} caught with the expected flag, {r.anomalies_not_auto_paid} held for a person or denied.
          </span>
        </div>
        <div className="proof-figs">
          <div className="fig">
            <span className="fig-label">Automation precision</span>
            <span className="fig-value tnum">{pct(r.automation_precision, 1)}</span>
            <span className="fig-sub ink-3 tnum">of {r.all_auto_paid ?? r.clean_auto_paid} payments made without a person, {r.anomalies_auto_paid === 0 ? 'none were' : `${r.anomalies_auto_paid} were`} planted errors</span>
          </div>
          <div className="fig">
            <span className="fig-label">Clean requests</span>
            <span className="fig-value tnum">{r.clean_total.toLocaleString('en-US')}</span>
            <span className="fig-sub ink-3 tnum">
              {r.clean_auto_paid} paid automatically, {r.clean_escalated} sent to a person
            </span>
          </div>
          <div className="fig">
            <span className="fig-label">Audit chain</span>
            <span className={`fig-value ${r.chain_ok ? 'ok' : 'deny'}`}>
              {r.chain_ok ? <IconCheck size={18} /> : <IconWarn size={18} />} {r.chain_ok ? 'Verified' : 'Broken'}
            </span>
            <span className="fig-sub ink-3 tnum">{r.chain_checked ? `${r.chain_checked.toLocaleString('en-US')} events recomputed after the run` : 'every hash recomputed after the run'}</span>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Automation before and after learning</h2>
          <span className="ink-3">Share of clean requests paid without a person</span>
        </div>
        <div className="learn-bars">
          <LearnBar label={`Days 1 to ${warm}, default thresholds`} sub={r.warmup_clean_purchases ? `${r.warmup_clean_purchases} clean purchases` : undefined} v={before} />
          <LearnBar label={`Days ${warm + 1} to ${days}, after the learned policies were approved`} sub={r.scored_clean_purchases ? `${r.scored_clean_purchases} clean purchases` : undefined} v={after} accent />
        </div>
        {learned && learned.length > 0 ? (
          <ul className="learned-policies">
            {learned.map((p) => (
              <li key={p.id}>
                <span className="mono ink-3">{p.id}</span>
                <span>{policySentence(p, world)}</span>
              </li>
            ))}
          </ul>
        ) : (
          Array.isArray(r.learned_policies) &&
          r.learned_policies.length > 0 && (
            <p className="ink-2 learned-list">
              Learned and approved during warm up{' '}
              {r.learned_policies.map((p, i) => (
                <span key={i} className="chip">
                  {typeof p === 'string' ? p : (p as { id?: string }).id ?? JSON.stringify(p)}
                </span>
              ))}
            </p>
          )
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">By planted category</h2>
          <span className={allCaught ? 'ok' : 'deny'}>{allCaught ? 'Every category caught' : 'Some categories missed'}</span>
        </div>
        <div className="table-wrap">
          <table className="table proof-table">
            <colgroup>
              <col style={{ width: '33%' }} />
              <col style={{ width: 84 }} />
              <col style={{ width: 84 }} />
              <col style={{ width: 112 }} />
              <col style={{ width: 92 }} />
              <col />
            </colgroup>
            <thead>
              <tr>
                <th>Category</th>
                <th className="num">Planted</th>
                <th className="num">Caught</th>
                <th className="num">Not auto paid</th>
                <th className="num">Auto paid</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {cats.map((c) => (
                <tr key={c.category}>
                  <td>
                    <span className="proof-cat">{CAT_LABEL[c.category] ?? c.category}</span> <span className="mono ink-3 proof-code">{c.category}</span>
                  </td>
                  <td className="num">{c.planted}</td>
                  <td className={`num${c.caught < c.planted ? ' cell-hold' : ''}`}>{c.caught}</td>
                  <td className="num">{c.not_auto_paid}</td>
                  <td className={`num${c.auto_paid > 0 ? ' cell-deny' : ' ok'}`}>{c.auto_paid}</td>
                  <td className="ink-2">
                    {c.notes ?? ''}
                    {Array.isArray(c.missed) && c.missed.length > 0 && <span className="deny"> Missed {c.missed.length}.</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>All categories</td>
                <td className="num">{cats.reduce((s, c) => s + c.planted, 0)}</td>
                <td className="num">{cats.reduce((s, c) => s + c.caught, 0)}</td>
                <td className="num">{cats.reduce((s, c) => s + c.not_auto_paid, 0)}</td>
                <td className="num">{cats.reduce((s, c) => s + c.auto_paid, 0)}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
        {r.method && <p className="method">{r.method}</p>}
        <p className="run-meta ink-3 tnum">
          Seed {r.seed}, {r.mode ?? 'default mode'}{r.fast ? ', fast mode' : ''}, {Math.round(r.runtime_seconds)} s, generated {dateTime(r.generated_at)}
          {r.engine_git_hash ? `, engine ${String(r.engine_git_hash).slice(0, 8)}` : ''}.
        </p>
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">What is real and what is simulated</h2>
        </div>
        <div className="truth">
          <div className="truth-col">
            <h3 className="truth-h">Real</h3>
            <ul>
              <li>The policy engine, its rules and the decisions on every request</li>
              <li>The double entry ledger and its balance checks</li>
              <li>The hash chained audit log, including refused attempts</li>
              <li>x402 payment messages and EIP-3009 signatures, built and verified with the official x402 SDK</li>
              <li>Claude agents, when an Anthropic key is set</li>
            </ul>
          </div>
          <div className="truth-col">
            <h3 className="truth-h">Simulated</h3>
            <ul>
              <li>Kestrel Labs, its people, vendors and history</li>
              <li>USDC settlement in offline mode, recorded in a local ledger</li>
              <li>Bank ACH payments</li>
              <li>Scripted agents when no key is set, labeled as scripted</li>
            </ul>
          </div>
          <div className="truth-col">
            <h3 className="truth-h">How we know</h3>
            <ul>
              <li>The answer key is generated separately and the engine never reads it</li>
              <li>Fixed seeds, so anyone can rerun and get the same report</li>
              <li>The run goes through the real offline x402 rail and vendor servers</li>
              <li>The audit chain is verified after every run</li>
            </ul>
          </div>
        </div>
      </section>
    </>
  );
}

function LearnBar({ label, sub, v, accent = false }: { label: string; sub?: string; v: number; accent?: boolean }) {
  return (
    <div className="learn-bar">
      <span className="learn-label">
        {label}
        {sub && <span className="learn-sub ink-3 tnum">{sub}</span>}
      </span>
      <span className="learn-track">
        <span className={`learn-fill${accent ? ' learn-fill-accent' : ''}`} style={{ width: `${Math.max(0.5, v * 100)}%` }} />
        {[25, 50, 75].map((t) => (
          <span key={t} className="learn-tick" style={{ left: `${t}%` }} />
        ))}
      </span>
      <span className="learn-v tnum">{pct(v, 1)}</span>
    </div>
  );
}
