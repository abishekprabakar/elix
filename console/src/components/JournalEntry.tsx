import { useState } from 'react';
import type { JournalEntry } from '../api/types';
import { calDate, money } from '../lib/format';
import { useStore } from '../lib/store';
import { ItemLink } from './Bits';
import { IconCheck } from './Icons';

export function JournalEntryBlock({
  je,
  onRecode,
  canRecode = false,
  periodClosed = false,
  highlight = false,
}: {
  je: JournalEntry;
  onRecode?: (lineIndex: number) => void;
  canRecode?: boolean;
  periodClosed?: boolean;
  highlight?: boolean;
}) {
  const { world } = useStore();
  const acct = (code: string) => world?.accounts.find((a) => a.code === code)?.name ?? '';
  const vend = (id?: string | null) => world?.vendors.find((v) => v.id === id)?.name ?? '';
  const dr = je.lines.reduce((s, l) => s + l.debit_cents, 0);
  const cr = je.lines.reduce((s, l) => s + l.credit_cents, 0);
  const balanced = dr === cr && dr > 0;
  const [hover, setHover] = useState<number | null>(null);

  return (
    <article className={`je${highlight ? ' je-highlight' : ''}${je.reversed_by ? ' je-reversed' : ''}`} id={je.id}>
      <header className="je-head">
        <span className="mono je-id">{je.id}</span>
        <span className="je-date tnum">{calDate(je.date)}</span>
        <span className="je-memo truncate">{je.memo}</span>
        {je.source_work_item && (
          <span className="je-source">
            <ItemLink id={je.source_work_item} />
          </span>
        )}
      </header>
      <table className="je-lines">
        <thead>
          <tr>
            <th className="je-th-code">Account</th>
            <th />
            <th className="je-cc je-th-cc">Cost center</th>
            <th className="num je-th-amt">Debit</th>
            <th className="num je-th-amt">Credit</th>
            {canRecode && <th className="je-th-act" />}
          </tr>
        </thead>
        <tbody>
          {je.lines
            .map((l, i) => ({ l, i }))
            .sort((a, b) => (a.l.debit_cents > 0 ? 0 : 1) - (b.l.debit_cents > 0 ? 0 : 1) || a.i - b.i)
            .map(({ l, i }) => (
            <tr key={i} className={l.credit_cents > 0 ? 'je-cr' : 'je-dr'} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <td className="mono je-code">{l.account}</td>
              <td className="je-name">
                <span>{acct(l.account)}</span>
                {(l.memo || l.vendor_id) && <span className="je-line-memo ink-3">{l.memo ?? vend(l.vendor_id)}</span>}
              </td>
              <td className="ink-2 je-cc">{l.cost_center ?? ''}</td>
              <td className="num">{l.debit_cents ? money(l.debit_cents) : ''}</td>
              <td className="num">{l.credit_cents ? money(l.credit_cents) : ''}</td>
              {canRecode && (
                <td className="je-act">
                  {l.debit_cents > 0 && !je.reversed_by && (
                    <button
                      type="button"
                      className={`btn btn-ghost btn-xs${hover === i ? ' is-visible' : ''}`}
                      onClick={() => onRecode?.(i)}
                      title={periodClosed ? 'The period is closed, so the fix posts in October' : 'Move this line to another account'}
                    >
                      Recode
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={2}>
              <span className={`je-balanced ${balanced ? 'ok' : 'deny'}`}>
                {balanced ? <IconCheck size={13} /> : null}
                {balanced ? 'Balanced' : 'Out of balance'}
              </span>
            </td>
            <td className="je-cc" />
            <td className="num je-total">{money(dr)}</td>
            <td className="num je-total">{money(cr)}</td>
            {canRecode && <td />}
          </tr>
        </tfoot>
      </table>
      {(je.reverses || je.reversed_by) && (
        <footer className="je-foot">
          {je.reverses && (
            <span>
              Reverses <ItemLink id={je.reverses} />
            </span>
          )}
          {je.reversed_by && (
            <span>
              Reversed by <ItemLink id={je.reversed_by} />
            </span>
          )}
        </footer>
      )}
    </article>
  );
}
