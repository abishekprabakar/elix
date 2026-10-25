import type { WorkItem } from '../api/types';

type Itemish = Pick<WorkItem, 'approvals' | 'decision'> & { data?: Record<string, unknown> | null; approved_by?: string | null };

/** True while a payee change still has to be verified by the controller. */
export function payeePending(it: Itemish): boolean {
  const hold = (it.data?.payee_hold as boolean | undefined) ?? it.decision?.payee_hold ?? false;
  if (!hold) return false;
  const verified = !!it.data?.payee_verified || (it.approvals ?? []).some((a) => a.kind === 'payee');
  return !verified;
}

export function hasPayeeHold(it: Itemish): boolean {
  return !!((it.data?.payee_hold as boolean | undefined) ?? it.decision?.payee_hold);
}

export function spendApproval(it: Itemish) {
  return (it.approvals ?? []).find((a) => a.kind === 'spend');
}

export function payeeApproval(it: Itemish) {
  return (it.approvals ?? []).find((a) => a.kind === 'payee');
}

export function spendApproved(it: Itemish): boolean {
  return !!it.data?.spend_approved || !!spendApproval(it);
}

/** Receipt ids and tx refs a reconciliation line found on another rail. */
export function lineReceipts(evidence: Record<string, unknown> | undefined): { id: string; tx: string | null }[] {
  if (!evidence) return [];
  const rs = evidence.receipts as { item_id?: string; tx_ref?: string }[] | undefined;
  if (Array.isArray(rs) && rs.length) return rs.filter((r) => r.item_id).map((r) => ({ id: r.item_id as string, tx: r.tx_ref ?? null }));
  const ids = (evidence.receipt_ids as string[] | undefined) ?? [];
  const txs = (evidence.tx_refs as string[] | undefined) ?? [];
  return ids.map((id, i) => ({ id, tx: txs[i] ?? null }));
}
