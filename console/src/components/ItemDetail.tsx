import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { Check, NextAction, Policy, VendorInvoice, WorkItem, WorkItemDetail, X402Panel } from '../api/types';
import { calDate, dateTime, money, periodLabel, qty } from '../lib/format';
import { hasPayeeHold, payeeApproval, payeePending, spendApproval, spendApproved } from '../lib/items';
import { useApi, useStore } from '../lib/store';
import { AuditRow } from './AuditRow';
import { Avatar, ErrorState, Flags, InlineError, ItemLink, Money, Section, Skeleton } from './Bits';
import { CheckList } from './CheckList';
import { DisputeBlock } from './CreditNotes';
import { IntakeSource } from './Intake';
import { ElixMark, IconCheck, IconClose, IconExpand, IconLock } from './Icons';
import { JournalEntryBlock } from './JournalEntry';
import { Pill } from './Pill';
import { ReceiptBlock } from './Receipt';
import { DisputeNote, ReconSummary, ReconTable } from './ReconTable';

const KIND_LABEL: Record<string, string> = { purchase: 'Purchase', invoice: 'Invoice', accrual: 'Accrual', correction: 'Correction', policy: 'Policy proposal' };
const REL_LABEL: Record<string, string> = { duplicate_of: 'Duplicate of', reverses: 'Reverses', reconciles: 'Reconciles against', accrues: 'Accrues', corrects: 'Corrects', precedent: 'Precedent', policy: 'Policy', dispute: 'Dispute', related: 'Related' };

const VERIFY_METHODS = ['Called vendor at number on contract', 'Confirmed in signed vendor portal', 'Other'];

export function ItemDetail({ id, inDrawer = false, onClose }: { id: string; inDrawer?: boolean; onClose?: () => void }) {
  const { data, error, loading, setData } = useApi<WorkItemDetail>(`/api/work-items/${id}`);
  const isX402 = !!data?.item.challenge;
  const { data: panel } = useApi<X402Panel>(isX402 ? `/api/work-items/${id}/x402` : null);

  if (loading && !data) return <DetailSkeleton />;
  if (error && !data) return <ErrorState error={error} what={id} />;
  if (!data) return null;
  return <DetailBody d={data} panel={panel} inDrawer={inDrawer} onClose={onClose} onUpdate={setData} />;
}

function DetailSkeleton() {
  return (
    <div className="detail">
      <div className="detail-head">
        <span className="skeleton-bar" style={{ width: 120, height: 12 }} />
        <span className="skeleton-bar" style={{ width: '60%', height: 24, marginTop: 10 }} />
      </div>
      <Skeleton rows={8} cols={3} />
    </div>
  );
}

function DetailBody({ d, panel, inDrawer, onClose, onUpdate }: { d: WorkItemDetail; panel: X402Panel | null; inDrawer: boolean; onClose?: () => void; onUpdate: (d: WorkItemDetail) => void }) {
  const { world, actorName } = useStore();
  const it = d.item;
  const vendor = world?.vendors.find((v) => v.id === it.vendor_id);
  const agent = world?.agents.find((a) => a.actor_id === it.requested_by);
  const invoice = it.data?.invoice as VendorInvoice | undefined;
  const isAgentItem = it.requested_by.startsWith('agent:');

  return (
    <div className={`detail${inDrawer ? ' detail-drawer' : ''}`}>
      <header className="detail-head">
        <div className="detail-top">
          <p className="detail-id">
            <span className="mono">{it.id}</span>
            <span className="ink-3">{KIND_LABEL[it.kind] ?? it.kind}</span>
            {it.simulated && it.kind !== 'policy' && <span className="badge-sim badge-sm">Simulated</span>}
          </p>
          {inDrawer && (
            <div className="detail-tools">
              <Link className="btn btn-ghost btn-sm" to={`/items/${it.id}`} title="Open as a page">
                <IconExpand size={14} /> Open page
              </Link>
              {onClose && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
                  Close
                </button>
              )}
            </div>
          )}
        </div>
        <div className="detail-title-row">
          <h1 className="detail-title">{it.title || it.id}</h1>
          {it.amount_cents > 0 && <Money cents={it.amount_cents} className="detail-amount" />}
        </div>
        <div className="detail-state">
          <Pill state={it.state} item={it} />
          <Flags flags={it.flags} max={6} />
        </div>
        <dl className="detail-meta">
          {agent && (
            <div>
              <dt>Agent</dt>
              <dd>
                <Avatar id={agent.actor_id} size={16} /> {agent.name}
                <span className="ink-3"> for {actorName(agent.owner_id)}</span>
              </dd>
            </div>
          )}
          {!agent && (
            <div>
              <dt>Requested by</dt>
              <dd>
                <Avatar id={it.requested_by} size={16} /> {actorName(it.requested_by)}
              </dd>
            </div>
          )}
          {vendor && (
            <div>
              <dt>Vendor</dt>
              <dd>{vendor.name}</dd>
            </div>
          )}
          <div>
            <dt>Period</dt>
            <dd>{periodLabel(it.period)}</dd>
          </div>
          {it.cost_center && (
            <div>
              <dt>Cost center</dt>
              <dd>{it.cost_center}</dd>
            </div>
          )}
          {it.account && (
            <div>
              <dt>Account</dt>
              <dd>
                <span className="mono">{it.account}</span> {world?.accounts.find((a) => a.code === it.account)?.name}
              </dd>
            </div>
          )}
          <div>
            <dt>Created</dt>
            <dd className="tnum">{dateTime(it.created_at)}</dd>
          </div>
        </dl>
        <Actions d={d} onUpdate={onUpdate} />
      </header>

      {d.explanation && it.kind === 'invoice' && <Explain text={d.explanation} />}

      {it.kind === 'invoice' && d.reconciliation && (
        <Section title="Three way match" aside={invoice && <span className="ink-2 tnum">Issued {calDate(invoice.issued_at)}, due {calDate(invoice.due_at)}</span>}>
          <ReconSummary rec={d.reconciliation} />
          <ReconTable rec={d.reconciliation} item={it} />
          {d.reconciliation.dispute_note && <DisputeNote note={d.reconciliation.dispute_note} />}
          <DisputeBlock d={d} onUpdate={onUpdate} />
        </Section>
      )}

      <div className="detail-cols">
      <div className="detail-col-main">
      {d.explanation && it.kind !== 'invoice' && <Explain text={d.explanation} />}
      <Approvals it={it} />
      {it.decision && it.decision.checks.length > 0 && (
        <Section title="Decision checks" aside={<span className="mono ink-3">{it.decision.policy_version}</span>}>
          <CheckList checks={it.decision.checks} />
        </Section>
      )}

      {it.kind === 'invoice' && !!it.data?.intake && (
        <Section title="Read from a PDF">
          <IntakeSource it={it} />
          {(it.data.intake as { status?: string }).status === 'accepted' && <CheckList checks={((it.data.intake as { checks?: Check[] }).checks ?? [])} compactPassed />}
        </Section>
      )}

      {it.kind === 'purchase' && (
        <Section title="Request">
          <dl className="kv kv-request">
            <div className="kv-row">
              <dt>Quantity</dt>
              <dd className="tnum">
                {qty(it.quantity)} {vendor?.skus.find((s) => s.sku === it.sku)?.unit ?? ''} at {money(it.unit_price_cents)} <span className="mono ink-3">{it.sku}</span>
              </dd>
            </div>
            <div className="kv-row">
              <dt>Purpose</dt>
              <dd>{it.purpose || <span className="ink-3">None given</span>}</dd>
            </div>
            <div className="kv-row">
              <dt>Task</dt>
              <dd>{it.task_ref ? <span className="mono">{it.task_ref}</span> : <span className="ink-3">None given</span>}</dd>
            </div>
            {it.idempotency_key && (
              <div className="kv-row">
                <dt>Idempotency key</dt>
                <dd className="mono">{it.idempotency_key}</dd>
              </div>
            )}
            {it.memo && (
              <div className="kv-row">
                <dt>Memo</dt>
                <dd>
                  <blockquote className="memo">{it.memo}</blockquote>
                  <span className="memo-note ink-3">Memo text is treated as data. Elix never follows instructions in it.</span>
                </dd>
              </div>
            )}
          </dl>
        </Section>
      )}

      {it.kind === 'correction' && <CorrectionBody it={it} />}
      {it.kind === 'policy' && <PolicyBody it={it} />}
      {it.kind === 'accrual' && <AccrualBody it={it} />}

      {(it.questions.length > 0 || isAgentItem) && <Questions d={d} onUpdate={onUpdate} />}

      <Section title="Timeline" aside={<span className="ink-3">{d.events.length} events</span>}>
        <ol className="audit-list audit-list-timeline">
          {d.events.map((e) => (
            <AuditRow key={e.seq} e={e} showDate />
          ))}
        </ol>
      </Section>

      </div>
      <div className="detail-col-side">
        {(it.challenge || (it.receipt && it.kind === 'purchase')) && (
          <div className="side-block">
            <ReceiptBlock challenge={it.challenge} receipt={it.receipt} vendor={vendor} panel={panel} payeeHold={payeePending(it) && !it.receipt} failed={it.state === 'FAILED' ? String(it.data?.error ?? '') : null} />
          </div>
        )}
        {it.kind === 'invoice' && it.receipt && (
          <Section title="Payment">
            <ReceiptBlock challenge={null} receipt={it.receipt} />
          </Section>
        )}
        {d.journal_entries.length > 0 && (
          <Section title="Journal entries" aside={<span className="ink-3">{d.journal_entries.length}</span>}>
            <div className="je-stack">
              {d.journal_entries.map((je) => (
                <JournalEntryBlock key={je.id} je={je} />
              ))}
            </div>
          </Section>
        )}
        {it.kind !== 'invoice' && !it.challenge && !it.receipt && d.journal_entries.length === 0 && (
          <Section title="Payment">
            <p className="ink-3 side-empty">{it.kind === 'policy' ? 'Policies never move money. Once active, this one lets Elix approve matching requests on its own, still subject to every other check.' : 'Nothing has been paid or posted for this item.'}</p>
          </Section>
        )}
        {(it.links.length > 0 || d.linked.length > 0) && <Linked it={it} linked={d.linked} />}
      </div>
      </div>
    </div>
  );
}

function Explain({ text }: { text: string }) {
  return (
    <div className="explain">
      <span className="explain-mark">
        <ElixMark size={16} />
      </span>
      <p>{text}</p>
    </div>
  );
}

function Approvals({ it }: { it: WorkItem }) {
  const { actorName, world } = useStore();
  if (it.kind !== 'purchase' || !it.decision || it.decision.outcome === 'auto_approve' || it.decision.outcome === 'deny') return null;
  const agent = world?.agents.find((a) => a.actor_id === it.requested_by);
  const spendRole = (it.data?.spend_role as string | undefined) ?? (it.decision.required_approver_role === 'controller' && !hasPayeeHold(it) ? 'controller' : 'budget_owner');
  const spend = spendApproval(it) ?? (spendApproved(it) || (it.approved_by && it.approved_by !== 'system:policy') ? { actor: it.approved_by ?? '', at: it.updated_at } : undefined);
  const payee = payeeApproval(it) ?? (hasPayeeHold(it) && !payeePending(it) ? { actor: 'human:priya', at: it.updated_at } : undefined);
  const rows: { label: string; who: string; done: { actor: string; at: string } | undefined }[] = [
    { label: 'Spend approval', who: spendRole === 'controller' ? 'Priya Raman' : actorName(agent?.owner_id), done: spend },
  ];
  if (hasPayeeHold(it)) rows.push({ label: 'Payee verification', who: 'Priya Raman', done: payee });
  const terminal = it.state === 'REJECTED' || it.state === 'DENIED' || it.state === 'FAILED';
  const failed = it.state === 'FAILED';
  return (
    <div className="approvals" aria-label="Approvals">
      {rows.map((r, i) => (
        <div key={i} className={`approval${r.done ? ' approval-done' : terminal ? ' approval-off' : ' approval-wait'}`}>
          <span className="approval-glyph">{r.done ? <IconCheck size={13} /> : r.label.startsWith('Payee') ? <IconLock size={13} /> : <span className="approval-ring" />}</span>
          <div>
            <p className="approval-label">{r.label}</p>
            <p className="approval-who ink-2">
              {r.done ? (
                <>
                  {actorName(r.done.actor)}, {dateTime(r.done.at)}
                </>
              ) : terminal ? (
                'Not needed'
              ) : (
                <>Waiting for {r.who}</>
              )}
            </p>
          </div>
        </div>
      ))}
      <div className={`approval approval-gate${it.receipt ? ' approval-done' : failed ? ' approval-failed' : ''}`}>
        <span className="approval-glyph">{it.receipt ? <IconCheck size={13} /> : failed ? <IconClose size={13} /> : <span className="approval-ring" />}</span>
        <div>
          <p className="approval-label">Gateway pays</p>
          <p className="approval-who ink-2">{it.receipt ? `${it.receipt.tx_ref}` : failed ? 'Payment failed, nothing moved' : terminal ? 'Will not pay' : 'After every approval'}</p>
        </div>
      </div>
    </div>
  );
}

export function Actions({ d, onUpdate }: { d: WorkItemDetail; onUpdate: (d: WorkItemDetail) => void }) {
  const { toast, session, refresh } = useStore();
  const [open, setOpen] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [method, setMethod] = useState(VERIFY_METHODS[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const it = d.item;
  useEffect(() => {
    setOpen(null);
    setErr(null);
    setNote('');
  }, [it.id, session?.actor.id]);

  const acts = d.next_actions ?? [];
  if (!acts.length) return null;

  const run = async (a: NextAction) => {
    setBusy(true);
    setErr(null);
    const path =
      a.action === 'verify_payee'
        ? `/api/work-items/${it.id}/verify-payee`
        : a.action === 'ask'
          ? `/api/work-items/${it.id}/ask`
          : `/api/work-items/${it.id}/${a.action}`;
    const body = a.action === 'verify_payee' ? { method, note } : a.action === 'ask' ? { text: note } : { note };
    try {
      let res: WorkItemDetail;
      if (it.kind === 'policy' && (a.action === 'approve' || a.action === 'reject') && it.data?.policy_id) {
        await api<Policy>(`/api/policies/${it.data.policy_id}/${a.action}`, { method: 'POST', body: { note } });
        res = await api<WorkItemDetail>(`/api/work-items/${it.id}`);
      } else {
        res = await api<WorkItemDetail>(path, { method: 'POST', body });
      }
      onUpdate(res);
      setOpen(null);
      setNote('');
      toast(doneText(a, it, res), res.item.state === 'FAILED' ? 'deny' : 'ok');
      refresh();
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const tryAnyway = async (a: NextAction) => {
    setBusy(true);
    setErr(null);
    const path = a.action === 'verify_payee' ? `/api/work-items/${it.id}/verify-payee` : `/api/work-items/${it.id}/${a.action}`;
    try {
      const res = await api<WorkItemDetail>(path, { method: 'POST', body: a.action === 'verify_payee' ? { method: VERIFY_METHODS[0], note: '' } : { note: '' } });
      onUpdate(res);
    } catch (e) {
      refresh();
      const ae = e as ApiError;
      setErr(ae.auditSeq ? `${ae.message} Refused by the API and logged as event ${ae.auditSeq.toLocaleString('en-US')}.` : ae.message);
    } finally {
      setBusy(false);
    }
  };

  const primary = (a: NextAction) => a.action === 'approve' || a.action === 'verify_payee';
  const needsNote = (a: NextAction) => a.action === 'reject' || a.action === 'ask';

  return (
    <div className="actions">
      <div className="actions-row">
        {acts.map((a) => (
          <button
            key={a.action}
            type="button"
            className={`btn ${primary(a) ? 'btn-primary' : a.action === 'reject' ? 'btn-danger' : 'btn-secondary'}${open === a.action ? ' is-active' : ''}`}
            disabled={!a.allowed || busy}
            aria-describedby={!a.allowed ? `why-${a.action}` : undefined}
            onClick={() => {
              setErr(null);
              setOpen((o) => (o === a.action ? null : a.action));
            }}
          >
            {a.label}
          </button>
        ))}
      </div>
      {acts
        .filter((a) => !a.allowed && a.reason)
        .map((a) => (
          <p key={a.action} id={`why-${a.action}`} className="action-reason">
            <IconLock size={12} />
            <span>
              <span className="action-reason-label">{a.label}.</span> {a.reason}
              {session?.actor.kind === 'human' && (a.action === 'approve' || a.action === 'verify_payee') && (
                <button type="button" className="btn btn-ghost btn-xs try-anyway" disabled={busy} onClick={() => tryAnyway(a)}>
                  Try anyway
                </button>
              )}
            </span>
          </p>
        ))}
      {open &&
        (() => {
          const a = acts.find((x) => x.action === open)!;
          return (
            <form
              className="action-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (needsNote(a) && !note.trim()) {
                  setErr(a.action === 'ask' ? 'Write the question first.' : 'Add a note so the agent and the auditor know why.');
                  return;
                }
                run(a);
              }}
            >
              {a.action === 'verify_payee' && (
                <label className="field">
                  <span className="field-label">How did you verify the new address?</span>
                  <select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
                    {VERIFY_METHODS.map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                  </select>
                </label>
              )}
              <label className="field">
                <span className="field-label">{a.action === 'ask' ? 'Question' : needsNote(a) ? 'Reason' : 'Note (optional)'}</span>
                <textarea
                  className="input"
                  rows={2}
                  value={note}
                  autoFocus
                  placeholder={a.action === 'verify_payee' ? 'Called Stormvault at the number on the 2025 contract' : a.action === 'ask' ? 'Which task is this for?' : ''}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <div className="action-form-row">
                <button type="submit" className={`btn ${a.action === 'reject' ? 'btn-danger-solid' : 'btn-primary'}`} disabled={busy}>
                  {busy ? 'Working' : confirmLabel(a)}
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => setOpen(null)}>
                  Cancel
                </button>
              </div>
            </form>
          );
        })()}
      <InlineError message={err} />
    </div>
  );
}

function confirmLabel(a: NextAction) {
  switch (a.action) {
    case 'approve':
      return a.label;
    case 'reject':
      return a.label;
    case 'verify_payee':
      return 'Verify payee and release payment';
    case 'ask':
      return 'Send question';
    default:
      return a.label;
  }
}

function doneText(a: NextAction, it: WorkItem, res: WorkItemDetail) {
  const after = res.item;
  if (after.state === 'FAILED') return 'Approved, but the payment failed and nothing was paid.';
  if (a.action === 'verify_payee') return after.receipt ? 'Payee verified. Payment sent.' : 'Payee verified.';
  if (a.action === 'ask') return 'Question sent.';
  if (a.action === 'reject') return it.kind === 'policy' ? 'Policy rejected.' : it.kind === 'invoice' ? 'Invoice rejected.' : 'Request rejected.';
  if (it.kind === 'invoice') return after.receipt ? `Short pay approved. ${money(after.receipt.amount_cents)} sent.` : 'Invoice approved.';
  if (it.kind === 'policy') return 'Policy approved.';
  if (it.kind === 'correction') return 'Correction approved.';
  if (after.receipt) return 'Payment approved and sent.';
  if (after.decision?.payee_hold) return 'Spend approved. Payment waits for payee verification.';
  return 'Approved.';
}

function Questions({ d, onUpdate }: { d: WorkItemDetail; onUpdate: (d: WorkItemDetail) => void }) {
  const { actorName } = useStore();
  void onUpdate;
  const qs = d.item.questions;
  return (
    <Section title="Questions" aside={<span className="ink-3">{qs.length}</span>}>
      {qs.length === 0 ? (
        <p className="ink-3">No questions on this item.</p>
      ) : (
        <ol className="qa">
          {qs.map((q) => (
            <li key={q.id} className="qa-item">
              <div className="qa-q">
                <Avatar id={q.asked_by} size={18} />
                <div>
                  <p className="qa-who">
                    {actorName(q.asked_by)} <span className="ink-3">asked {actorName(q.to)}, {dateTime(q.at)}</span>
                  </p>
                  <p>{q.text}</p>
                </div>
              </div>
              {q.answer ? (
                <div className="qa-a">
                  <Avatar id={q.answered_by ?? q.to} size={18} />
                  <div>
                    <p className="qa-who">
                      {actorName(q.answered_by ?? q.to)} <span className="ink-3">answered{q.answered_at ? `, ${dateTime(q.answered_at)}` : ''}</span>
                    </p>
                    <p>{q.answer}</p>
                  </div>
                </div>
              ) : (
                <p className="qa-wait ink-3">Waiting for an answer</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

function Linked({ it, linked }: { it: WorkItem; linked: WorkItem[] }) {
  const rows = [
    ...it.links.map((l) => ({ rel: l.rel, id: l.id, item: linked.find((x) => x.id === l.id) })),
    ...linked.filter((x) => !it.links.some((l) => l.id === x.id)).map((x) => ({ rel: 'related' as const, id: x.id, item: x, back: true })),
  ];
  return (
    <Section title="Linked">
      <ul className="linked">
        {rows.map((r, i) => (
          <li key={i}>
            <span className="linked-rel ink-2">{(r as { back?: boolean }).back ? 'Referenced by' : REL_LABEL[r.rel] ?? r.rel}</span>
            <ItemLink id={r.id} />
            {r.item && (
              <>
                <span className="truncate">{r.item.title}</span>
                <Pill state={r.item.state} item={r.item} />
              </>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function CorrectionBody({ it }: { it: WorkItem }) {
  const { world } = useStore();
  const dd = it.data as { je_id?: string; from_account?: string; to_account?: string; note?: string; closed_period?: boolean; original_period?: string };
  const acct = (c?: string) => world?.accounts.find((a) => a.code === c)?.name ?? '';
  return (
    <Section title="Correction">
      <div className="recode">
        <div className="recode-from">
          <span className="fig-label">From</span>
          <span>
            <span className="mono">{dd.from_account}</span> {acct(dd.from_account)}
          </span>
        </div>
        <svg width="28" height="12" viewBox="0 0 28 12" aria-hidden="true" className="recode-arrow">
          <path d="M1 6h24M20 1.5 25 6l-5 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
        <div className="recode-to">
          <span className="fig-label">To</span>
          <span>
            <span className="mono">{dd.to_account}</span> {acct(dd.to_account)}
          </span>
        </div>
      </div>
      <dl className="kv">
        <div className="kv-row">
          <dt>Original entry</dt>
          <dd>{dd.je_id && <ItemLink id={dd.je_id} />}</dd>
        </div>
        {dd.note && (
          <div className="kv-row">
            <dt>Note</dt>
            <dd>{dd.note}</dd>
          </div>
        )}
        {dd.closed_period && (
          <div className="kv-row">
            <dt>Posting</dt>
            <dd>
              <span className="closed-note">
                <IconLock size={12} /> The original period is closed, so the fix posts in the open period as a reversal and a repost.
              </span>
            </dd>
          </div>
        )}
      </dl>
    </Section>
  );
}

function PolicyBody({ it }: { it: WorkItem }) {
  const { data } = useApi<{ policies: Policy[] }>('/api/policies');
  const p = data?.policies.find((x) => x.id === it.data?.policy_id);
  if (!p) return null;
  return (
    <Section title="Policy" aside={<Link className="link" to={`/policies#${p.id}`}>See all policies</Link>}>
      <dl className="kv">
        <div className="kv-row">
          <dt>Rule</dt>
          <dd>{p.rationale}</dd>
        </div>
        <div className="kv-row">
          <dt>Evidence</dt>
          <dd className="tnum">
            {p.evidence.precedent_count ?? 0} precedents, {p.evidence.window}, {p.evidence.rejections ?? 0} rejected
          </dd>
        </div>
      </dl>
    </Section>
  );
}

function AccrualBody({ it }: { it: WorkItem }) {
  const a = (it.data?.assumptions ?? it.data) as Record<string, unknown>;
  const sentence = (a?.sentence as string) ?? '';
  return (
    <Section title="Assumptions">
      <p className="prose">{sentence}</p>
      <pre className="code code-sm">{JSON.stringify(a, null, 2)}</pre>
    </Section>
  );
}
