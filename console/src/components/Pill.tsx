import type { State, WorkItem } from '../api/types';
import { payeePending } from '../lib/items';

type PillKind = 'posted' | 'paid' | 'auto' | 'approved' | 'needs' | 'payee' | 'info' | 'proposed' | 'denied' | 'rejected' | 'failed' | 'new' | 'active' | 'retired';

const LABEL: Record<PillKind, string> = {
  posted: 'Posted',
  paid: 'Paid',
  auto: 'Auto approved',
  approved: 'Approved',
  needs: 'Needs approval',
  payee: 'Held for payee check',
  info: 'Needs info',
  proposed: 'Proposed',
  denied: 'Denied',
  rejected: 'Rejected',
  failed: 'Failed',
  new: 'New',
  active: 'Active',
  retired: 'Retired',
};

type PillItem = Pick<WorkItem, 'approved_by' | 'decision' | 'approvals' | 'kind'> & { data?: Record<string, unknown> | null };

export function pillKind(state: State, item?: PillItem): PillKind {
  switch (state) {
    case 'POSTED':
      return 'posted';
    case 'PAID':
      return 'paid';
    case 'APPROVED':
      return item?.approved_by === 'system:policy' ? 'auto' : 'approved';
    case 'NEEDS_APPROVAL':
      if (item && payeePending(item)) return 'payee';
      return 'needs';
    case 'NEEDS_INFO':
      return 'info';
    case 'DENIED':
      return 'denied';
    case 'REJECTED':
      return 'rejected';
    case 'FAILED':
      return 'failed';
    case 'NEW':
      return 'new';
    default:
      return 'proposed';
  }
}

export function stateLabel(state: State, item?: PillItem): string {
  return LABEL[pillKind(state, item)];
}

function Glyph({ kind }: { kind: PillKind }) {
  const c = { width: 10, height: 10, viewBox: '0 0 10 10', 'aria-hidden': true as const };
  switch (kind) {
    case 'posted':
    case 'paid':
      return (
        <svg {...c}>
          <path d="m2 5.25 2 2 4-4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case 'auto':
    case 'approved':
    case 'active':
      return (
        <svg {...c}>
          <circle cx="5" cy="5" r="3" fill="currentColor" />
        </svg>
      );
    case 'needs':
      return (
        <svg {...c}>
          <circle cx="5" cy="5" r="3.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="M5 1.75a3.25 3.25 0 0 1 0 6.5Z" fill="currentColor" />
        </svg>
      );
    case 'payee':
      return (
        <svg {...c}>
          <rect x="1.75" y="4.5" width="6.5" height="4.25" rx="0.9" fill="currentColor" />
          <path d="M3.25 4.5V3.4a1.75 1.75 0 0 1 3.5 0v1.1" fill="none" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      );
    case 'info':
      return (
        <svg {...c}>
          <circle cx="5" cy="5" r="3.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <circle cx="5" cy="5" r="1.1" fill="currentColor" />
        </svg>
      );
    case 'denied':
    case 'failed':
      return (
        <svg {...c}>
          <path d="m2.5 2.5 5 5M7.5 2.5l-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      );
    case 'rejected':
    case 'retired':
      return (
        <svg {...c}>
          <circle cx="5" cy="5" r="3.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="m2.9 7.1 4.2-4.2" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      );
    default:
      return (
        <svg {...c}>
          <circle cx="5" cy="5" r="3.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      );
  }
}

export function Pill({ state, item, kind, label }: { state?: State; item?: PillItem; kind?: PillKind; label?: string }) {
  const k = kind ?? pillKind(state ?? 'PROPOSED', item);
  return (
    <span className={`pill pill-${k}`}>
      <Glyph kind={k} />
      <span>{label ?? LABEL[k]}</span>
    </span>
  );
}
