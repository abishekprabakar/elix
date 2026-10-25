import type { Check, Severity } from '../api/types';

const ORDER: Record<Severity, number> = { deny: 0, escalate: 1, ask: 2, info: 3 };

export function CheckGlyph({ passed, severity }: { passed: boolean; severity: Severity }) {
  const c = { width: 14, height: 14, viewBox: '0 0 14 14', 'aria-hidden': true as const };
  if (passed)
    return (
      <svg {...c} className="glyph glyph-pass">
        <circle cx="7" cy="7" r="5" fill="currentColor" />
      </svg>
    );
  if (severity === 'deny')
    return (
      <svg {...c} className="glyph glyph-deny">
        <path d="m3.5 3.5 7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  if (severity === 'escalate')
    return (
      <svg {...c} className="glyph glyph-hold">
        <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M7 2.25a4.75 4.75 0 0 1 0 9.5Z" fill="currentColor" />
      </svg>
    );
  if (severity === 'ask')
    return (
      <svg {...c} className="glyph glyph-hold">
        <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="7" cy="7" r="1.6" fill="currentColor" />
      </svg>
    );
  return (
    <svg {...c} className="glyph glyph-note">
      <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function headline(checks: Check[]) {
  const failed = checks.filter((c) => !c.passed && c.severity !== 'info').length;
  const noted = checks.filter((c) => !c.passed && c.severity === 'info').length;
  const passed = checks.filter((c) => c.passed).length;
  if (!failed && !noted) return `All ${checks.length} checks passed.`;
  const parts: string[] = [];
  if (failed) parts.push(`${failed} ${failed === 1 ? 'check' : 'checks'} failed`);
  if (noted) parts.push(`${noted} noted`);
  return `${parts.join(' and ')}. ${passed} passed.`;
}

const STATUS: Record<string, string> = { deny: 'Denies', escalate: 'Escalates', ask: 'Asks', info: 'Noted' };

export function CheckList({ checks, compactPassed = false }: { checks: Check[]; compactPassed?: boolean }) {
  const failed = checks.filter((c) => !c.passed).sort((a, b) => ORDER[a.severity] - ORDER[b.severity]);
  const passed = checks.filter((c) => c.passed);
  const row = (c: Check) => (
    <li key={c.rule_id} className={`check ${c.passed ? 'check-pass' : `check-fail check-${c.severity}`}`}>
      <CheckGlyph passed={c.passed} severity={c.severity} />
      <div className="check-text">
        <span className="check-name">{c.name}</span>
        <span className="check-detail">{c.detail}</span>
      </div>
      <span className="check-status">{c.passed ? 'Passed' : STATUS[c.severity]}</span>
      <span className="check-rule mono">{c.rule_id}</span>
    </li>
  );
  return (
    <div className="checks">
      <div className="checks-head">
        <span>{headline(checks)}</span>
      </div>
      <ul className={`check-list${compactPassed ? ' check-list-compact' : ''}`}>
        {failed.map(row)}
        {passed.map(row)}
      </ul>
    </div>
  );
}
