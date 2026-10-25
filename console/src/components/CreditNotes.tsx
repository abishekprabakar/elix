import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { CreditNote, DisputeStatus, VendorInvoice, WorkItem, WorkItemDetail } from '../api/types';
import { calDate, money } from '../lib/format';
import { useStore } from '../lib/store';
import { InlineError, ItemLink } from './Bits';
import { Pill } from './Pill';

/** Dispute fields the engine writes on an approved invoice. Older items fall back to dispute_cents. */
export function disputeOf(it: Pick<WorkItem, 'kind' | 'state' | 'data'>) {
  const d = (it.data ?? {}) as Record<string, unknown>;
  const disputed = Number(d.dispute_cents) || 0;
  if (it.kind !== 'invoice' || !disputed) return null;
  const notes = ((d.credit_notes as CreditNote[] | undefined) ?? []).filter((n) => n.je_id);
  const credited = notes.reduce((t, n) => t + (n.applied_cents || 0), 0);
  const approved = it.state === 'POSTED';
  const open = approved ? (d.dispute_open_cents != null ? Number(d.dispute_open_cents) : Math.max(0, disputed - credited)) : disputed;
  const status: DisputeStatus | 'proposed' = !approved ? 'proposed' : ((d.dispute_status as DisputeStatus | undefined) ?? (open === 0 ? 'credited' : credited ? 'partly_credited' : 'open'));
  return { disputed, credited, open, status, notes, approved };
}

export function DisputePill({ status }: { status: DisputeStatus | 'proposed' }) {
  if (status === 'credited') return <Pill kind="posted" label="Credited" />;
  if (status === 'partly_credited') return <Pill kind="needs" label="Partly credited" />;
  if (status === 'open') return <Pill kind="needs" label="Dispute open" />;
  return <Pill kind="proposed" label="Proposed" />;
}

function toCents(text: string): number | null {
  const clean = text.replace(/[$,\s]/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  const [whole, frac = ''] = clean.split('.');
  return Number(whole) * 100 + Number((frac + '00').slice(0, 2));
}

function dollars(cents: number): string {
  return (cents / 100).toFixed(2);
}

/** The dispute on an approved invoice: its status, the credit notes applied, and a form to record one. */
export function DisputeBlock({ d, onUpdate }: { d: WorkItemDetail; onUpdate: (d: WorkItemDetail) => void }) {
  const { session, world, actorName } = useStore();
  const it = d.item;
  const dispute = disputeOf(it);
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [it.id, session?.actor.id]);
  if (!dispute || !dispute.approved) return null;
  const vendor = world?.vendors.find((v) => v.id === it.vendor_id)?.name ?? 'The vendor';
  const inv = it.data?.invoice as VendorInvoice | undefined;
  const number = inv?.invoice_number ?? String(it.data?.invoice_number ?? '');
  const canRecord = !!session?.can.record_credit_notes && dispute.open > 0;
  const entries = new Map(d.journal_entries.map((je) => [je.id, je]));
  return (
    <section className="credit" aria-label="Dispute">
      <div className="credit-head">
        <div className="credit-head-main">
          <span className="credit-title">Dispute</span>
          <DisputePill status={dispute.status} />
        </div>
        <dl className="credit-figs tnum">
          <div>
            <dt>Disputed</dt>
            <dd>{money(dispute.disputed)}</dd>
          </div>
          <div>
            <dt>Credited</dt>
            <dd>{money(dispute.credited)}</dd>
          </div>
          <div className={dispute.open ? 'credit-open' : ''}>
            <dt>Still open</dt>
            <dd>{money(dispute.open)}</dd>
          </div>
        </dl>
      </div>
      {dispute.notes.length > 0 ? (
        <div className="credit-table-wrap">
        <table className="table credit-table tnum">
          <colgroup>
            <col style={{ width: '26%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '16%' }} />
            <col style={{ width: '18%' }} />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th>Credit note</th>
              <th>Issued</th>
              <th className="num">Amount</th>
              <th>Recorded by</th>
              <th>Journal entry</th>
            </tr>
          </thead>
          <tbody>
            {dispute.notes.map((n) => (
              <tr key={n.credit_number}>
                <td className="mono">{n.credit_number}</td>
                <td>{calDate(n.issued_at)}</td>
                <td className="num">{money(n.amount_cents)}</td>
                <td>{actorName(n.recorded_by)}</td>
                <td>
                  {n.je_id && <ItemLink id={n.je_id} />}
                  {n.je_id && entries.get(n.je_id) && <span className="credit-sub ink-3">Dr 2010, Cr 2150 on {calDate(entries.get(n.je_id)!.date)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : (
        <p className="credit-wait ink-2">
          {money(dispute.open)} stays in 2150 Vendor disputes until {vendor} sends a credit note for {number}.
        </p>
      )}
      {canRecord && !open && (
        <div className="credit-actions">
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOpen(true)}>
            Record credit note
          </button>
        </div>
      )}
      {canRecord && open && <CreditForm it={it} number={number} vendor={vendor} openCents={dispute.open} onUpdate={onUpdate} onClose={() => setOpen(false)} />}
    </section>
  );
}

function CreditForm({ it, number, vendor, openCents, onUpdate, onClose }: { it: WorkItem; number: string; vendor: string; openCents: number; onUpdate: (d: WorkItemDetail) => void; onClose: () => void }) {
  const { toast, refresh } = useStore();
  const [creditNumber, setCreditNumber] = useState('');
  const [amount, setAmount] = useState(dollars(openCents));
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    const cents = toCents(amount);
    if (!creditNumber.trim()) {
      setErr(`Enter the credit note number exactly as ${vendor} printed it.`);
      return;
    }
    if (cents == null || cents <= 0) {
      setErr('Enter the credited amount in dollars, for example 1260.00.');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api<WorkItemDetail>(`/api/invoices/${encodeURIComponent(number)}/credit-notes`, {
        method: 'POST',
        body: { vendor_id: it.vendor_id, credit_number: creditNumber.trim(), amount_cents: cents, reason: reason.trim() },
      });
      onUpdate(res);
      onClose();
      const left = disputeOf(res.item)?.open ?? 0;
      toast(left ? `Credit note recorded. ${money(left)} is still in dispute.` : 'Credit note recorded. Dispute closed.', 'ok');
      refresh();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.auditSeq ? `${ae.message} Refused and logged as event ${ae.auditSeq.toLocaleString('en-US')}.` : ae.message);
      refresh();
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="action-form credit-form"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <p className="ink-2">
        Elix checks the credit against {number} and books Dr 2010 Accounts payable, Cr 2150 Vendor disputes. {money(openCents)} is open.
      </p>
      <div className="credit-fields">
        <label className="field">
          <span className="field-label">Credit note number</span>
          <input className="input mono" value={creditNumber} autoFocus placeholder="LUM-CN-2026-10A" onChange={(e) => setCreditNumber(e.target.value)} />
        </label>
        <label className="field credit-amount">
          <span className="field-label">Amount in dollars</span>
          <input className="input tnum" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span className="field-label">Reason (optional)</span>
        <input className="input" value={reason} placeholder="Credit for the hours already paid over x402" onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="action-form-row">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? 'Working' : 'Record credit note'}
        </button>
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
      <InlineError message={err} />
    </form>
  );
}
