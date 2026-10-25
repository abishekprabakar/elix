import type { ReconciliationProposal, WorkItem } from '../api/types';
import { money, qty } from '../lib/format';
import { lineReceipts } from '../lib/items';
import { CopyButton, ItemLink } from './Bits';

/** Proposal strip: what Elix proposes to pay, what it disputes, and the invoice total. */
export function ReconSummary({ rec }: { rec: ReconciliationProposal }) {
  const disputed = rec.dispute_cents > 0;
  const payPct = rec.invoice_total_cents ? (rec.pay_cents / rec.invoice_total_cents) * 100 : 100;
  return (
    <div className="recon-summary">
      <div className="recon-figs">
        <div className="fig">
          <span className="fig-label">Pay now</span>
          <span className="fig-value tnum">{money(rec.pay_cents)}</span>
        </div>
        <div className={`fig${disputed ? ' fig-deny' : ''}`}>
          <span className="fig-label">Dispute</span>
          <span className="fig-value tnum">{money(rec.dispute_cents)}</span>
        </div>
        <div className="fig fig-quiet">
          <span className="fig-label">Invoice total</span>
          <span className="fig-value tnum">{money(rec.invoice_total_cents)}</span>
        </div>
      </div>
      <div className="recon-split" aria-hidden="true">
        <span className="recon-split-pay" style={{ width: `${payPct}%` }} />
        {disputed && <span className="recon-split-dispute" style={{ width: `${100 - payPct}%` }} />}
      </div>
      <p className="recon-sentence">{rec.summary}</p>
    </div>
  );
}

/** The quantity bar for a line: what the meter supports, and what was already paid on another rail. */
function QtyBar({ invoiced, expected, elsewhere }: { invoiced: number; expected: number; elsewhere: number }) {
  const over = Math.max(0, invoiced - expected - elsewhere);
  const total = Math.max(invoiced, expected + elsewhere);
  const w = (n: number) => `${(n / total) * 100}%`;
  return (
    <span className="qtybar" aria-hidden="true">
      <span className="qtybar-ok" style={{ width: w(Math.min(expected, invoiced)) }} />
      {elsewhere > 0 && <span className="qtybar-elsewhere" style={{ width: w(elsewhere) }} />}
      {over > 0 && <span className="qtybar-over" style={{ width: w(over) }} />}
    </span>
  );
}

export function ReconTable({ rec, item }: { rec: ReconciliationProposal; item?: WorkItem }) {
  const receiptIds = (l: ReconciliationProposal['lines'][number]) => lineReceipts(l.evidence).map((r) => r.id);
  const txRefs = (l: ReconciliationProposal['lines'][number]) => lineReceipts(l.evidence).map((r) => r.tx).filter(Boolean) as string[];
  return (
    <div className="recon-table-wrap">
      <table className="table recon-table">
        <thead>
          <tr>
            <th>Line</th>
            <th className="num">Invoiced</th>
            <th className="num">Rate</th>
            <th className="num">Metered</th>
            <th className="num">Paid on x402</th>
            <th className="num">Expected</th>
            <th className="num">Variance</th>
            <th>Finding</th>
          </tr>
        </thead>
        <tbody>
          {rec.lines.map((l) => {
            const variance = l.variance_cents !== 0;
            const ids = receiptIds(l);
            return (
              <tr key={l.line_id} className={variance ? 'recon-var' : ''}>
                <td>
                  <span className="mono ink-3 recon-lid">{l.line_id}</span>
                  <span className="recon-desc">{l.description}</span>
                  {l.expected_qty != null && l.invoiced_qty > 1 && <QtyBar invoiced={l.invoiced_qty} expected={l.expected_qty} elsewhere={l.paid_elsewhere_qty} />}
                </td>
                <td className="num">{qty(l.invoiced_qty)}</td>
                <td className={`num${l.rate_ok ? '' : ' cell-hold'}`}>
                  {money(l.invoiced_rate_cents)}
                  {!l.rate_ok && l.contract_rate_cents != null && <span className="cell-sub">contract {money(l.contract_rate_cents)}</span>}
                </td>
                <td className="num">{l.metered_qty == null ? <span className="ink-3">No meter</span> : qty(l.metered_qty)}</td>
                <td className={`num${l.paid_elsewhere_qty > 0 ? ' cell-deny' : ''}`}>
                  {l.paid_elsewhere_qty > 0 ? qty(l.paid_elsewhere_qty) : <span className="ink-3">0</span>}
                  {ids.length > 0 && (
                    <span className="cell-sub">
                      {ids.map((id) => (
                        <ItemLink key={id} id={id} />
                      ))}
                    </span>
                  )}
                </td>
                <td className="num">{l.expected_qty == null ? '' : qty(l.expected_qty)}</td>
                <td className={`num${variance ? (l.paid_elsewhere_qty > 0 ? ' cell-deny' : ' cell-hold') : ''}`}>{variance ? money(l.variance_cents) : <span className="ink-3">None</span>}</td>
                <td className="recon-finding">
                  {l.finding}
                  {txRefs(l).length > 0 && <span className="mono ink-3 recon-tx">{txRefs(l).join(', ')}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            <td colSpan={5} />
            <td className={`num${rec.dispute_cents ? ' cell-deny' : ''}`}>{rec.dispute_cents ? money(rec.dispute_cents) : <span className="ink-3">None</span>}</td>
            <td className="ink-2">{item ? `Invoice ${rec.invoice_number}` : ''}</td>
          </tr>
        </tfoot>
      </table>
      {rec.lines.some((l) => l.paid_elsewhere_qty > 0) && (
        <p className="qtybar-legend ink-2">
          <span className="key key-ok" /> Supported by the meter <span className="key key-elsewhere" /> Already paid over x402 <span className="key key-over" /> Over the meter
        </p>
      )}
    </div>
  );
}

export function DisputeNote({ note }: { note: string }) {
  return (
    <div className="dispute">
      <div className="dispute-head">
        <span>Dispute note draft</span>
        <CopyButton text={note} label="Copy note" />
      </div>
      <pre className="dispute-body">{note}</pre>
    </div>
  );
}
