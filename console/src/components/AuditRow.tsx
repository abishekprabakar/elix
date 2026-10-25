import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { AuditEvent } from '../api/types';
import { date, time } from '../lib/format';
import { Avatar } from './Bits';

export function AuditRow({ e, fresh = false, showDate = false, compact = false, onOpenSubject }: { e: AuditEvent; fresh?: boolean; showDate?: boolean; compact?: boolean; onOpenSubject?: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const hasDiff = e.diff && Object.keys(e.diff).length > 0;
  const hasEv = e.evidence && Object.keys(e.evidence).length > 0;
  const isItem = /^WI-\d+/.test(e.subject);
  return (
    <li className={`audit-row${e.allowed ? '' : ' audit-denied'}${fresh ? ' is-fresh' : ''}${compact ? ' audit-compact' : ''}`}>
      <span className="audit-seq tnum">{e.seq.toLocaleString('en-US')}</span>
      <span className="audit-time tnum">
        {showDate && <span className="audit-date">{date(e.at)}</span>}
        {time(e.at)}
      </span>
      <Avatar id={e.actor} />
      <div className="audit-main">
        <p className="audit-sentence">
          {!e.allowed && <span className="audit-refused">Refused</span>}
          {e.reason}
        </p>
        {!compact && (
          <p className="audit-meta">
            {isItem && onOpenSubject ? (
              <button type="button" className="idlink mono linklike" onClick={() => onOpenSubject(e.subject)}>
                {e.subject}
              </button>
            ) : isItem ? (
              <Link className="idlink mono" to={`/items/${e.subject}`}>
                {e.subject}
              </Link>
            ) : (
              <span className="mono ink-3">{e.subject}</span>
            )}
            <span className="mono ink-3">{e.action}</span>
            {(hasDiff || hasEv) && (
              <button type="button" className="btn btn-ghost btn-xs" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
                {open ? 'Hide diff' : 'Show diff'}
              </button>
            )}
          </p>
        )}
        {open && (
          <pre className="code code-sm">
            {JSON.stringify({ ...(hasDiff ? { diff: e.diff } : {}), ...(hasEv ? { evidence: e.evidence } : {}), prev_hash: e.prev_hash, hash: e.hash }, null, 2)}
          </pre>
        )}
      </div>
      <span className="audit-hash mono" title={e.hash}>
        {e.hash.slice(0, 8)}
      </span>
    </li>
  );
}
