import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { BalancesResponse, JournalEntry, LedgerList, WorkItemDetail } from '../api/types';
import { ErrorState, InlineError, Skeleton } from '../components/Bits';
import { IconCheck, IconLock, IconWarn } from '../components/Icons';
import { JournalEntryBlock } from '../components/JournalEntry';
import { dateYear, money, periodLabel, periodMonth } from '../lib/format';
import { useApi, useStore } from '../lib/store';

export function Ledger() {
  const { period, world, session, actorName } = useStore();
  const [account, setAccount] = useState('');
  const { data, error, loading } = useApi<LedgerList>(period ? `/api/ledger?period=${period}&limit=300${account ? `&account=${account}` : ''}` : null);
  const { data: bal } = useApi<BalancesResponse>(period ? `/api/balances?period=${period}` : null);
  const per = world?.periods.find((p) => p.id === period);
  const closed = per?.status === 'closed';
  const canRecode = !!session?.can.recode;
  const [recoding, setRecoding] = useState<{ je: JournalEntry; line: number } | null>(null);
  const focus = new URLSearchParams(window.location.search).get('je');

  useEffect(() => {
    if (focus && data) document.getElementById(focus)?.scrollIntoView({ block: 'center' });
  }, [focus, data]);

  const rows = useMemo(() => (bal?.balances ?? []).map((b) => ({ ...b, net: b.debit_cents - b.credit_cents })).filter((b) => b.net !== 0), [bal]);
  const totals = useMemo(() => {
    const d = rows.reduce((s, b) => s + Math.max(0, b.net), 0);
    const c = rows.reduce((s, b) => s + Math.max(0, -b.net), 0);
    return { d, c };
  }, [rows]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Ledger</h1>
          <p className="page-sub">
            Journal entries for {period ? periodLabel(period) : 'this period'}, newest first. Only the Elix gateway posts, and entries are never edited.
          </p>
        </div>
        <select className="input" value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Account">
          <option value="">All accounts</option>
          {world?.accounts.map((a) => (
            <option key={a.code} value={a.code}>
              {a.code} {a.name}
            </option>
          ))}
        </select>
      </header>
      {closed && per && (
        <div className="period-banner">
          <IconLock size={14} />
          <span>
            {periodLabel(per.id)} is closed. {actorName(per.closed_by)} closed it on {dateYear(per.closed_at)}. Fixes post in the open period as a reversal and a repost.
          </span>
        </div>
      )}
      <ErrorState error={error} what="the ledger" />
      <div className="ledger-grid">
        <div className="ledger-entries">
          {recoding && <RecodeForm je={recoding.je} line={recoding.line} closed={!!closed} onDone={() => setRecoding(null)} />}
          {loading && !data ? (
            <Skeleton rows={10} cols={5} />
          ) : data?.entries.length === 0 ? (
            <div className="panel empty-panel">
              <p className="empty-title">No entries in {periodLabel(period)}.</p>
            </div>
          ) : (
            <div className="je-stack">
              {data?.entries.map((je) => (
                <JournalEntryBlock key={je.id} je={je} canRecode={canRecode} periodClosed={!!closed} highlight={focus === je.id} onRecode={(line) => setRecoding({ je, line })} />
              ))}
            </div>
          )}
          {data && data.total > data.entries.length && <p className="ink-3 list-more">Showing {data.entries.length} of {data.total} entries.</p>}
        </div>
        <aside className="ledger-side">
          <div className="tb panel">
            <div className="tb-head">
              <h2 className="section-title">Trial balance</h2>
              {bal && (
                <span className={`tb-state ${bal.trial_balance_ok ? 'ok' : 'deny'}`}>
                  {bal.trial_balance_ok ? <IconCheck size={13} /> : <IconWarn size={13} />}
                  {bal.trial_balance_ok ? 'Balanced' : 'Out of balance'}
                </span>
              )}
            </div>
            {!bal ? (
              <Skeleton rows={8} cols={3} />
            ) : (
              <table className="tb-table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th className="num">Debit</th>
                    <th className="num">Credit</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.account} className={account === b.account ? 'is-selected' : ''} onClick={() => setAccount((a) => (a === b.account ? '' : b.account))}>
                      <td>
                        <span className="tnum ink-3 tb-code">{b.account}</span> <span className="tb-name">{b.name}</span>
                      </td>
                      <td className="num">{b.net > 0 ? money(b.net) : ''}</td>
                      <td className="num">{b.net < 0 ? money(-b.net) : ''}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total</td>
                    <td className="num">{money(totals.d)}</td>
                    <td className="num">{money(totals.c)}</td>
                  </tr>
                </tfoot>
              </table>
            )}
            <p className="tb-note ink-3">Balances through the end of {period ? periodMonth(period) : 'the period'}, including the July 1 opening balances. Select an account to filter the entries.</p>
          </div>
        </aside>
      </div>
    </div>
  );
}

function RecodeForm({ je, line, closed, onDone }: { je: JournalEntry; line: number; closed: boolean; onDone: () => void }) {
  const { world, toast, period, refresh } = useStore();
  const l = je.lines[line];
  const [to, setTo] = useState(l.account === '6110' ? '6210' : '6110');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [made, setMade] = useState<WorkItemDetail | null>(null);
  const expense = world?.accounts.filter((a) => a.type === 'expense' && a.code !== l.account) ?? [];
  const submit = async () => {
    if (!note.trim()) {
      setErr('Add a note so the approver and the auditor know why.');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await api<WorkItemDetail>(`/api/ledger/${je.id}/recode`, { method: 'POST', body: { line_index: line, new_account: to, note } });
      setMade(r);
      refresh();
      toast(`Correction proposed. ${r.item.id} needs approval.`, 'ok');
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="recode-form panel">
      <div className="recode-form-head">
        <h2 className="section-title">
          Recode <span className="mono">{je.id}</span> line {line + 1}
        </h2>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>
          Close
        </button>
      </div>
      {made ? (
        <p>
          Elix created <Link className="idlink mono" to={`/items/${made.item.id}`}>{made.item.id}</Link>. {closed ? `${periodMonth(je.period)} is closed, so on approval it posts a reversal and a repost in ${periodMonth(world?.periods.find((p) => p.status === 'open')?.id ?? period)}.` : 'On approval it posts a reversal and a corrected repost today.'} Someone other than you has to approve it.
        </p>
      ) : (
        <>
          <p className="ink-2">
            Moves {money(l.debit_cents)} from <span className="mono">{l.account}</span> {world?.accounts.find((a) => a.code === l.account)?.name}.
            {closed ? ` ${periodLabel(je.period)} is closed, so Elix will not edit the entry. It posts a reversal and a repost in the open period instead.` : ' Elix posts a reversal and a corrected repost dated today.'}
          </p>
          <div className="recode-fields">
            <label className="field">
              <span className="field-label">New account</span>
              <select className="input" value={to} onChange={(e) => setTo(e.target.value)}>
                {expense.map((a) => (
                  <option key={a.code} value={a.code}>
                    {a.code} {a.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field field-grow">
              <span className="field-label">Note</span>
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Firmographics for outreach belong in marketing data" />
            </label>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={submit}>
              {busy ? 'Working' : 'Propose correction'}
            </button>
          </div>
          <InlineError message={err} />
        </>
      )}
    </div>
  );
}
