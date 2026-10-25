import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { ApiError } from '../api/client';
import type { FlagCode } from '../api/types';
import { money } from '../lib/format';
import { useCopy } from '../lib/hooks';
import { useStore } from '../lib/store';
import { IconCheck, IconCopy, IconWarn } from './Icons';

export function Money({ cents, className = '', sign = false }: { cents: number | null | undefined; className?: string; sign?: boolean }) {
  if (cents == null) return <span className={`money ${className}`} />;
  const s = money(Math.abs(cents));
  return <span className={`money tnum ${className}`}>{cents < 0 ? `(${s})` : sign && cents > 0 ? `+${s}` : s}</span>;
}

export const FLAG_LABEL: Record<FlagCode, [string, string]> = {
  PERMISSION: ['R01', 'Not permitted'],
  UNKNOWN_VENDOR: ['R02', 'Unknown vendor'],
  PAYEE_CHANGED: ['R03', 'Payee changed'],
  DUPLICATE_RETRY: ['R04', 'Duplicate retry'],
  AMOUNT_MISMATCH: ['R05', 'Amount mismatch'],
  PRICE_ABOVE_CONTRACT: ['R06', 'Above contract'],
  OVER_TXN_CAP: ['R07', 'Over cap'],
  BUDGET_EXCEEDED: ['R08', 'Over budget'],
  SPLIT_PURCHASE: ['R09', 'Split purchase'],
  MISSING_PURPOSE: ['R10', 'No purpose'],
  MEMO_INSTRUCTIONS: ['R11', 'Instructions in memo'],
  INVOICE_QTY_OVER_METER: ['R20', 'Over meter'],
  INVOICE_RATE_MISMATCH: ['R21', 'Rate mismatch'],
  CROSS_RAIL_DOUBLE_BILL: ['R22', 'Double billed'],
  DUPLICATE_INVOICE: ['R23', 'Duplicate invoice'],
  PERIOD_CLOSED: ['R30', 'Period closed'],
  SEGREGATION: ['R31', 'Segregation'],
};

export function FlagChip({ flag }: { flag: FlagCode }) {
  const [rule, label] = FLAG_LABEL[flag] ?? ['', flag.toLowerCase().replace(/_/g, ' ')];
  return (
    <span className="chip" title={`${rule} ${label}`}>
      <span className="chip-rule mono">{rule}</span>
      {label}
    </span>
  );
}

export function Flags({ flags, max = 3 }: { flags: FlagCode[]; max?: number }) {
  if (!flags?.length) return null;
  const shown = flags.slice(0, max);
  return (
    <span className="chips">
      {shown.map((f) => (
        <FlagChip key={f} flag={f} />
      ))}
      {flags.length > max && <span className="chip chip-more">{flags.length - max} more</span>}
    </span>
  );
}

export function Avatar({ id, size = 20 }: { id: string; size?: number }) {
  const { actor } = useStore();
  const a = actor(id);
  const style = { width: size, height: size };
  if (id.startsWith('agent:')) {
    return (
      <span className="avatar avatar-agent" style={style} title={a?.name ?? id}>
        <svg width={size * 0.36} height={size * 0.62} viewBox="0 0 6 10" aria-hidden="true">
          <rect x="0.6" y="0.6" width="4.8" height="8.8" rx="2.4" fill="none" stroke="currentColor" strokeWidth="1.2" />
          <rect x="0.6" y="4.6" width="4.8" height="4.8" rx="2.4" fill="currentColor" />
        </svg>
      </span>
    );
  }
  if (id.startsWith('system:')) {
    return (
      <span className="avatar avatar-system" style={style} title={a?.name ?? id}>
        {id === 'system:sync' ? (
          <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M13 6.5A5 5 0 0 0 3.6 4.9M3 9.5a5 5 0 0 0 9.4 1.6" />
          </svg>
        ) : (
          <svg width={size * 0.34} height={size * 0.6} viewBox="0 0 6 10" aria-hidden="true">
            <rect x="0.6" y="0.6" width="4.8" height="8.8" rx="2.4" fill="none" stroke="currentColor" strokeWidth="1.2" />
            <rect x="0.6" y="3.4" width="4.8" height="6" rx="2.4" fill="currentColor" />
          </svg>
        )}
      </span>
    );
  }
  const name = a?.name ?? id.replace(/^\w+:/, '');
  const initials = name
    .split(' ')
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
  return (
    <span className={`avatar avatar-human avatar-${id.replace(':', '-')}`} style={{ ...style, fontSize: size * 0.42 }} title={name}>
      {initials}
    </span>
  );
}

export function ItemLink({ id, children }: { id: string; children?: ReactNode }) {
  const isJe = id.startsWith('JE-');
  const isPol = id.startsWith('pol-');
  const to = isJe ? `/ledger?je=${id}` : isPol ? `/policies#${id}` : `/items/${id}`;
  return (
    <Link className="idlink mono" to={to}>
      {children ?? id}
    </Link>
  );
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const { copied, copy } = useCopy();
  return (
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => copy(text)}>
      {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
      {copied ? 'Copied' : label}
    </button>
  );
}

export function Skeleton({ rows = 6, cols = 5, className = '' }: { rows?: number; cols?: number; className?: string }) {
  return (
    <div className={`skeleton ${className}`} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, r) => (
        <div className="skeleton-row" key={r}>
          {Array.from({ length: cols }, (_, c) => (
            <span key={c} className="skeleton-bar" style={{ width: `${[18, 34, 22, 14, 12, 26][(r + c) % 6]}%` }} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ErrorState({ error, what }: { error: ApiError | null; what?: string }) {
  if (!error) return null;
  return (
    <div className="error-state" role="alert">
      <IconWarn size={16} />
      <div>
        <p className="error-title">{what ? `Could not load ${what}.` : 'Could not load this page.'}</p>
        <p className="ink-2">{error.message}</p>
      </div>
    </div>
  );
}

export function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="inline-error" role="alert">
      <IconWarn size={14} />
      <span>{message}</span>
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body ink-2">{children}</div>}
    </div>
  );
}

export function Section({ title, aside, children, id, className = '' }: { title: ReactNode; aside?: ReactNode; children: ReactNode; id?: string; className?: string }) {
  return (
    <section className={`section ${className}`} id={id}>
      <div className="section-head">
        <h2 className="section-title">{title}</h2>
        {aside && <div className="section-aside">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function Toggle({ label, children, initial = false }: { label: [string, string]; children: ReactNode; initial?: boolean }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="toggle">
      <button type="button" className="btn btn-ghost btn-sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? label[1] : label[0]}
      </button>
      {open && children}
    </div>
  );
}

export function Kv({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div className="kv-row" key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
