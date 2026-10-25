import { useEffect, useMemo, useState } from 'react';
import type { ReconciliationProposal, VendorInvoice, WorkItem, WorkItemDetail } from '../api/types';
import { ErrorState, ItemLink, Money, Skeleton } from '../components/Bits';
import { DisputeBlock, disputeOf } from '../components/CreditNotes';
import { IntakePanel } from '../components/Intake';
import { Actions } from '../components/ItemDetail';
import { Pill } from '../components/Pill';
import { DisputeNote, ReconSummary, ReconTable } from '../components/ReconTable';
import { calDate, money, periodLabel } from '../lib/format';
import { useApi, useStore } from '../lib/store';

type Row = { item: WorkItem; reconciliation: ReconciliationProposal | null };

function covers(inv?: VendorInvoice) {
  const w = inv?.lines.find((l) => l.usage_window)?.usage_window;
  if (w) {
    const end = new Date(new Date(w.end).getTime() - 1000).toISOString();
    return `${calDate(w.start)} to ${calDate(end)}`;
  }
  return inv ? periodLabel(inv.period) : '';
}

function finding(r: Row): string {
  const rec = r.reconciliation;
  const dispute = disputeOf(r.item);
  if (dispute?.status === 'credited') return 'Credited back by the vendor';
  if (dispute?.status === 'partly_credited') return `Partly credited, ${money(dispute.open)} open`;
  if (rec?.dispute_cents) return 'Already paid over x402';
  return rec?.lines.some((l) => l.variance_cents) ? 'Over the meter' : 'Matches meter and contract';
}

export function Reconcile() {
  const { period, world, session } = useStore();
  const [uploading, setUploading] = useState(false);
  const canUpload = !!session?.can.upload_invoices;
  const { data, error, loading } = useApi<{ items: Row[] }>(period ? `/api/reconciliations?period=${period}` : null);
  const rows = useMemo(() => {
    const r = data?.items ?? [];
    return [...r].sort((a, b) => {
      const pa = a.item.state === 'NEEDS_APPROVAL' ? 0 : 1;
      const pb = b.item.state === 'NEEDS_APPROVAL' ? 0 : 1;
      if (pa !== pb) return pa - pb;
      return a.item.created_at < b.item.created_at ? 1 : -1;
    });
  }, [data]);
  const [sel, setSel] = useState<string | null>(() => new URLSearchParams(window.location.search).get('invoice'));
  useEffect(() => {
    if (rows.length && (!sel || !rows.some((r) => r.item.id === sel))) {
      const pick = rows.find((r) => (r.reconciliation?.dispute_cents ?? 0) > 0 || r.item.state === 'NEEDS_APPROVAL') ?? rows[0];
      setSel(pick.item.id);
    }
  }, [rows, sel]);

  const variance = rows.filter((r) => (r.reconciliation?.dispute_cents ?? 0) > 0 || r.reconciliation?.lines.some((l) => l.variance_cents !== 0));
  const disputed = rows.reduce((s, r) => s + (r.reconciliation?.dispute_cents ?? 0), 0);
  const total = rows.reduce((s, r) => s + (r.reconciliation?.invoice_total_cents ?? r.item.amount_cents), 0);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Reconcile</h1>
          <p className="page-sub">Invoices for {period ? periodLabel(period) : 'this period'}, matched against usage meters and payments on every rail.</p>
          {canUpload && !uploading && (
            <button type="button" className="btn btn-secondary btn-sm page-head-action" onClick={() => setUploading(true)}>
              Upload an invoice PDF
            </button>
          )}
        </div>
        <div className="head-figs tnum">
          <div className="fig fig-sm">
            <span className="fig-label">Invoices</span>
            <span className="fig-value">{rows.length}</span>
          </div>
          <div className="fig fig-sm">
            <span className="fig-label">Invoiced</span>
            <span className="fig-value">{money(total)}</span>
          </div>
          <div className={`fig fig-sm${disputed ? ' fig-deny' : ''}`}>
            <span className="fig-label">With variance</span>
            <span className="fig-value">{variance.length}</span>
          </div>
          <div className={`fig fig-sm${disputed ? ' fig-deny' : ''}`}>
            <span className="fig-label">Disputed</span>
            <span className="fig-value">{money(disputed)}</span>
          </div>
        </div>
      </header>
      {canUpload && uploading && (
        <IntakePanel
          onClose={() => setUploading(false)}
          onDone={(item) => {
            if (item.state !== 'NEEDS_INFO') setSel(item.id);
          }}
        />
      )}
      <ErrorState error={error} what="invoices" />
      {(loading && !data) || !period ? (
        <Skeleton rows={5} cols={6} />
      ) : rows.length === 0 ? (
        <div className="panel empty-panel">
          <p className="empty-title">No invoices in {periodLabel(period)} yet.</p>
          <p className="ink-2">Run the Lumen scenario from Scenarios to bring in invoice LUM-2026-10A.</p>
        </div>
      ) : (
        <>
          <div className="table-wrap">
            <table className="table invoice-table">
              <colgroup>
                <col style={{ width: 172 }} />
                <col style={{ width: 176 }} />
                <col style={{ width: 128 }} />
                <col style={{ width: 118 }} />
                <col style={{ width: 124 }} />
                <col style={{ width: 104 }} />
                <col />
                <col style={{ width: 156 }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Invoice</th>
                  <th>Covers</th>
                  <th className="num">Invoiced</th>
                  <th className="num">Proposed pay</th>
                  <th className="num">Dispute</th>
                  <th>Finding</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const inv = r.item.data?.invoice as VendorInvoice | undefined;
                  const v = world?.vendors.find((x) => x.id === r.item.vendor_id);
                  const rec = r.reconciliation;
                  return (
                    <tr key={r.item.id} className={`row-click${sel === r.item.id ? ' is-selected' : ''}`} onClick={() => setSel(r.item.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setSel(r.item.id)}>
                      <td>{v?.name}</td>
                      <td className="mono">{inv?.invoice_number ?? rec?.invoice_number}</td>
                      <td className="tnum ink-2">{covers(inv)}</td>
                      <td className="num">{money(rec?.invoice_total_cents ?? r.item.amount_cents)}</td>
                      <td className="num">{rec ? money(rec.pay_cents) : ''}</td>
                      <td className={`num${rec?.dispute_cents ? ' cell-deny' : ''}`}>{rec?.dispute_cents ? money(rec.dispute_cents) : <span className="ink-3">None</span>}</td>
                      <td className="truncate cell-finding ink-2">{finding(r)}</td>
                      <td>
                        <Pill state={r.item.state} item={r.item} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {sel && <InvoicePanel id={sel} />}
        </>
      )}
    </div>
  );
}

function InvoicePanel({ id }: { id: string }) {
  const { data, setData } = useApi<WorkItemDetail>(`/api/work-items/${id}`);
  const { world } = useStore();
  if (!data) return <Skeleton rows={6} cols={6} className="panel" />;
  const it = data.item;
  const inv = it.data?.invoice as VendorInvoice | undefined;
  const v = world?.vendors.find((x) => x.id === it.vendor_id);
  const rec = data.reconciliation;
  return (
    <section className="invoice-panel panel">
      <header className="invoice-head">
        <div>
          <p className="detail-id">
            <span className="mono">{it.id}</span>
            <span className="ink-3">Invoice</span>
          </p>
          <h2 className="invoice-title">
            {v?.name} invoice <span className="mono">{inv?.invoice_number}</span>
          </h2>
          <p className="ink-2 tnum">
            Covers {covers(inv)}. Issued {inv ? calDate(inv.issued_at) : ''}, due {inv ? calDate(inv.due_at) : ''}.
          </p>
        </div>
        <div className="invoice-head-right">
          <Pill state={it.state} item={it} />
          <Money cents={it.amount_cents} className="invoice-total" />
        </div>
      </header>
      {rec && <ReconSummary rec={rec} />}
      <Actions d={data} onUpdate={setData} />
      {rec && <ReconTable rec={rec} item={it} />}
      {rec?.dispute_note && <DisputeNote note={rec.dispute_note} />}
      <DisputeBlock d={data} onUpdate={setData} />
      {data.journal_entries.length > 0 && (
        <p className="invoice-posted ink-2">
          Posted as {data.journal_entries.map((j, i) => (
            <span key={j.id}>
              {i > 0 && ' and '}
              <ItemLink id={j.id} />
            </span>
          ))}
          .
        </p>
      )}
    </section>
  );
}
