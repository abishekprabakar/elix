import { useRef, useState } from 'react';
import { apiBlob, apiUpload, ApiError } from '../api/client';
import type { IntakeResponse, IntakeSample, WorkItem } from '../api/types';
import { calDate, money, qty } from '../lib/format';
import { useApi, useStore } from '../lib/store';
import { InlineError, ItemLink } from './Bits';
import { CheckList } from './CheckList';
import { Pill } from './Pill';
import { ReconSummary, ReconTable } from './ReconTable';

function day(iso: string | null | undefined) {
  return iso ? calDate(`${iso}T12:00:00Z`) : '';
}

function extractorText(mode: string | undefined) {
  if (mode === 'claude') return 'Claude reads the PDF and proposes the fields. Elix checks every one of them in code before anything is booked.';
  return 'The offline PDF parser reads it, since no Anthropic key is set. Elix checks every field in code before anything is booked.';
}

/** Upload an invoice PDF on the Reconcile screen: extracted fields, each check, then the reconciliation or what failed. */
export function IntakePanel({ onClose, onDone }: { onClose: () => void; onDone: (item: WorkItem) => void }) {
  const { toast, refresh, actorName } = useStore();
  const { data: samples } = useApi<{ samples: IntakeSample[]; extractor: string }>('/api/intake/samples', { live: false });
  const file = useRef<HTMLInputElement>(null);
  const [sample, setSample] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<IntakeResponse | null>(null);

  const send = async (blob: Blob, name: string) => {
    setBusy(true);
    setErr(null);
    try {
      const form = new FormData();
      form.append('file', blob, name);
      const r = await apiUpload<IntakeResponse>('/api/invoices/pdf', form);
      setRes(r);
      const failed = r.intake.checks.filter((c) => !c.passed).length;
      toast(r.intake.accepted ? `Invoice read and reconciled as ${r.item.item.id}.` : `Invoice held. ${failed} ${failed === 1 ? 'check' : 'checks'} failed and nothing posted.`, r.intake.accepted ? 'ok' : 'deny');
      onDone(r.item.item);
      refresh();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.auditSeq ? `${ae.message} Refused and logged as event ${ae.auditSeq.toLocaleString('en-US')}.` : ae.message);
    } finally {
      setBusy(false);
      if (file.current) file.current.value = '';
    }
  };

  const useSample = async () => {
    const pick = sample || samples?.samples[0]?.name;
    if (!pick) return;
    setErr(null);
    try {
      const blob = await apiBlob(`/api/intake/samples/${encodeURIComponent(pick)}`);
      await send(blob, pick);
    } catch (e) {
      setErr((e as ApiError).message);
    }
  };

  return (
    <section className="intake panel" aria-label="Upload an invoice PDF">
      <header className="intake-head">
        <div>
          <h2 className="section-title">Upload an invoice PDF</h2>
          <p className="ink-2 intake-lede">{extractorText(samples?.extractor)} If a check fails, the invoice waits for {actorName('human:sam') || 'the accountant'} and nothing posts.</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="intake-controls">
        <input
          ref={file}
          type="file"
          accept="application/pdf,.pdf"
          className="intake-file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) send(f, f.name);
          }}
        />
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => file.current?.click()}>
          {busy ? 'Reading the PDF' : 'Choose a PDF'}
        </button>
        {samples && samples.samples.length > 0 && (
          <div className="intake-sample">
            <label className="field">
              <span className="field-label">Or upload a synthetic sample</span>
              <select className="input" value={sample || samples.samples[0].name} onChange={(e) => setSample(e.target.value)}>
                {samples.samples.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.title}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={useSample}>
              Upload sample
            </button>
          </div>
        )}
      </div>
      {samples && (
        <p className="ink-3 intake-hint">{samples.samples.find((s) => s.name === (sample || samples.samples[0]?.name))?.description}</p>
      )}
      <InlineError message={err} />
      {res && <IntakeOutcome r={res} />}
    </section>
  );
}

function IntakeOutcome({ r }: { r: IntakeResponse }) {
  const { actorName } = useStore();
  const ex = r.intake.extraction;
  const it = r.item.item;
  const failed = r.intake.checks.filter((c) => !c.passed);
  const vendor = r.intake.vendor;
  return (
    <div className="intake-result">
      <div className="intake-result-head">
        <span className="mono intake-file-name">{r.intake.filename}</span>
        <span className={`badge-extractor badge-${ex.extractor}`}>{ex.extractor === 'claude' ? ex.extractor_label : 'Offline PDF parser'}</span>
        {r.intake.accepted ? <Pill kind="approved" label="Accepted" /> : <Pill kind="info" label={`${failed.length} ${failed.length === 1 ? 'check' : 'checks'} failed`} />}
      </div>
      {ex.notes.length > 0 && <p className="ink-3 intake-notes">{ex.notes.join(' ')}</p>}
      <div className="intake-grid">
        <dl className="kv intake-fields">
          <div className="kv-row">
            <dt>Vendor</dt>
            <dd>
              {ex.vendor_name || <span className="ink-3">Not found</span>}
              {vendor && vendor.name !== ex.vendor_name && <span className="ink-3"> matched to {vendor.name}</span>}
              {vendor && <span className="mono ink-3"> {vendor.id}</span>}
            </dd>
          </div>
          <div className="kv-row">
            <dt>Invoice number</dt>
            <dd className="mono">{ex.invoice_number || <span className="ink-3">Not found</span>}</dd>
          </div>
          <div className="kv-row">
            <dt>Issued and due</dt>
            <dd className="tnum">
              {day(ex.issue_date) || 'No date'}, due {day(ex.due_date) || 'no date'}
            </dd>
          </div>
          <div className="kv-row">
            <dt>Billing period</dt>
            <dd className="tnum">{ex.period_start ? `${day(ex.period_start)} to ${day(ex.period_end)}` : <span className="ink-3">Not printed</span>}</dd>
          </div>
          <div className="kv-row">
            <dt>Total</dt>
            <dd className="tnum">{r.intake.total_cents != null ? money(r.intake.total_cents) : ex.total || <span className="ink-3">Not found</span>}</dd>
          </div>
        </dl>
        <CheckList checks={r.intake.checks} compactPassed />
      </div>
      <div className="table-wrap intake-lines">
        <table className="table">
          <colgroup>
            <col style={{ width: '28%' }} />
            <col style={{ width: '18%' }} />
            <col style={{ width: '9%' }} />
            <col style={{ width: '10%' }} />
            <col style={{ width: '11%' }} />
            <col style={{ width: '11%' }} />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th>Line</th>
              <th>Contract product</th>
              <th className="num">Quantity</th>
              <th className="num">Rate</th>
              <th className="num">Amount</th>
              <th className="num">Quantity times rate</th>
              <th>Service dates</th>
            </tr>
          </thead>
          <tbody>
            {r.intake.lines.map((l) => (
              <tr key={l.line_id} className={l.arithmetic_ok === false ? 'recon-var' : ''}>
                <td>
                  <span className="mono ink-3 recon-lid">{l.line_id}</span>
                  {l.description}
                </td>
                <td>
                  {l.sku ? <span className="mono">{l.sku}</span> : <span className="cell-deny">No match</span>}
                  {l.sku_source && <span className="intake-sub ink-3">by {l.sku_source}</span>}
                </td>
                <td className="num">{l.quantity != null ? qty(l.quantity) : ''}</td>
                <td className="num">{l.unit_price_cents != null ? money(l.unit_price_cents) : ''}</td>
                <td className={`num${l.arithmetic_ok === false ? ' cell-deny' : ''}`}>{l.amount_cents != null ? money(l.amount_cents) : ''}</td>
                <td className="num">{l.expected_cents != null ? money(l.expected_cents) : ''}</td>
                <td className="tnum ink-2">
                  {l.usage_start ? `${day(l.usage_start)} to ${day(l.usage_end)}` : <span className="ink-3">None</span>}
                  {l.window_source === 'billing period' && <span className="intake-sub ink-3">from the billing period</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.intake.accepted && r.item.reconciliation ? (
        <div className="intake-recon">
          <p className="intake-next">
            Every check passed, so Elix created <ItemLink id={it.id} /> and reconciled it against the contract, the usage meter and x402 receipts.
          </p>
          <ReconSummary rec={r.item.reconciliation} />
          <ReconTable rec={r.item.reconciliation} item={it} />
        </div>
      ) : (
        <p className="intake-next intake-held">
          Nothing was reconciled, paid or posted. <ItemLink id={it.id} /> is waiting for {actorName('human:sam')}, who can reject it and ask {vendor?.name ?? 'the vendor'} for a corrected invoice.
        </p>
      )}
    </div>
  );
}

/** Where an invoice came from, on the item page, when it was read from a PDF. */
export function IntakeSource({ it }: { it: WorkItem }) {
  const rec = it.data?.intake as { filename?: string; extractor?: string; extractor_label?: string; sha256?: string; uploaded_by?: string; uploaded_at?: string; notes?: string[]; status?: string } | undefined;
  const { actorName } = useStore();
  if (!rec) return null;
  return (
    <dl className="kv intake-source">
      <div className="kv-row">
        <dt>Source</dt>
        <dd>
          <span className="mono">{rec.filename}</span>
          <span className={`badge-extractor badge-${rec.extractor}`}>{rec.extractor === 'claude' ? rec.extractor_label : 'Offline PDF parser'}</span>
        </dd>
      </div>
      <div className="kv-row">
        <dt>Uploaded by</dt>
        <dd>
          {actorName(rec.uploaded_by)}
          {rec.sha256 && <span className="mono ink-3"> sha256 {rec.sha256.slice(0, 12)}</span>}
        </dd>
      </div>
      {rec.notes && rec.notes.length > 0 && (
        <div className="kv-row">
          <dt>Notes</dt>
          <dd className="ink-2">{rec.notes.join(' ')}</dd>
        </div>
      )}
    </dl>
  );
}
