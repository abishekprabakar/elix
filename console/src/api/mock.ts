// Fixture mode only (VITE_FIXTURES=1). An in-memory stand in for the API in specs/04,
// built from src/fixtures/*.json. It mirrors shapes, not engine behavior: decisions,
// checks and reconciliations come from the generated fixtures.
import worldJ from '../fixtures/world.json';
import itemsJ from '../fixtures/work-items.json';
import ledgerJ from '../fixtures/ledger.json';
import auditJ from '../fixtures/audit.json';
import policiesJ from '../fixtures/policies.json';
import evalJ from '../fixtures/eval-report.json';
import scenariosJ from '../fixtures/scenarios.json';
import sessionsJ from '../fixtures/sessions.json';
import { emit } from './events';
import { hasPayeeHold, payeePending, spendApproved } from '../lib/items';
import type {
  AgentSessionDetail,
  AuditEvent,
  JournalEntry,
  NextAction,
  Policy,
  ReconciliationProposal,
  Scenario,
  Summary,
  WhyReport,
  WorkItem,
  WorkItemDetail,
  World,
} from './types';

const world = worldJ as unknown as World;
let items = itemsJ as unknown as WorkItem[];
const ledger = ledgerJ as unknown as JournalEntry[];
const audit = auditJ as unknown as AuditEvent[];
const policies = policiesJ as unknown as Policy[];
const scenarios = scenariosJ as unknown as Scenario[];
const sessions = sessionsJ as unknown as AgentSessionDetail[];

const TOKENS: Record<string, string> = {
  tok_priya: 'human:priya', tok_dev: 'human:dev', tok_maya: 'human:maya', tok_sam: 'human:sam', tok_jordan: 'human:jordan',
  tok_atlas: 'agent:atlas', tok_forge: 'agent:forge', tok_scout: 'agent:scout',
};
const actor = (id: string) => world.actors.find((a) => a.id === id)!;
const name = (id: string) => world.actors.find((a) => a.id === id)?.name ?? id;
const agentOf = (id: string) => world.agents.find((a) => a.actor_id === id);
let clock = world.company.today;
const tick = (sec = 20) => {
  clock = new Date(new Date(clock).getTime() + sec * 1000).toISOString().replace('.000Z', 'Z');
  return clock;
};

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
const ok = (body: unknown, status = 200) => ({ status, body });
const err = (status: number, code: string, message: string, rule_id?: string, audit_seq?: number) => ({ status, body: { error: { code, message, rule_id: rule_id ?? null, audit_seq: audit_seq ?? null } } });

function appendEvent(e: Omit<AuditEvent, 'seq' | 'prev_hash' | 'hash'>) {
  const last = audit[audit.length - 1];
  const seq = last.seq + 1;
  const hash = fakeHash(JSON.stringify(e) + last.hash);
  const full: AuditEvent = { ...e, seq, prev_hash: last.hash, hash };
  audit.push(full);
  emit({ kind: 'audit', data: full });
  return full;
}
function fakeHash(s: string) {
  // fixture only: deterministic 64 hex chars
  let h1 = 0x811c9dc5, h2 = 0x01000193;
  let out = '';
  for (let r = 0; r < 8; r++) {
    for (let i = 0; i < s.length; i++) {
      h1 = Math.imul(h1 ^ s.charCodeAt(i), 16777619) >>> 0;
      h2 = Math.imul(h2 ^ (s.charCodeAt(i) + r), 2246822519) >>> 0;
    }
    out += ((h1 ^ h2) >>> 0).toString(16).padStart(8, '0');
  }
  return out;
}

// ------------------------------------------------------------ permissions (approximate 01)
function isOwner(actorId: string, it: WorkItem) {
  return agentOf(it.requested_by)?.owner_id === actorId;
}
function inboxFor(actorId: string, it: WorkItem): boolean {
  const role = actor(actorId)?.role;
  if (it.kind === 'purchase') {
    if (it.state !== 'NEEDS_APPROVAL') return false;
    if (hasPayeeHold(it)) {
      if (role === 'controller') return payeePending(it);
      if (role === 'budget_owner') return isOwner(actorId, it) && !spendApproved(it);
      return false;
    }
    if (role === 'budget_owner') return isOwner(actorId, it);
    return false;
  }
  if (it.kind === 'invoice') return it.state === 'NEEDS_APPROVAL' && role === 'accountant';
  if (it.kind === 'correction') return it.state === 'PROPOSED' && (role === 'controller' || role === 'accountant') && it.proposed_by !== actorId;
  if (it.kind === 'policy' || it.kind === 'accrual') return it.state === 'PROPOSED' && role === 'controller';
  return false;
}

function nextActions(actorId: string, it: WorkItem): NextAction[] {
  const a = actor(actorId);
  const role = a.role;
  const out: NextAction[] = [];
  const auditor = role === 'auditor';
  const readOnly = 'Auditors have read only access.';
  if (it.kind === 'purchase' && it.state === 'NEEDS_APPROVAL') {
    const owner = agentOf(it.requested_by)!;
    const ownerName = name(owner.owner_id);
    if (!spendApproved(it)) {
      const can = role === 'budget_owner' && isOwner(actorId, it);
      out.push({ action: 'approve', label: 'Approve spend', allowed: can, reason: can ? '' : auditor ? readOnly : `${ownerName} owns ${owner.name}'s budget, so this approval has to come from ${ownerName.split(' ')[0] === 'Maya' ? 'her' : 'him'}.` });
    }
    if (payeePending(it)) {
      const can = role === 'controller';
      out.push({ action: 'verify_payee', label: 'Verify payee', allowed: can, reason: can ? '' : auditor ? readOnly : 'Payee changes need the controller.' });
    }
    const canReject = (role === 'budget_owner' && isOwner(actorId, it)) || role === 'controller';
    out.push({ action: 'reject', label: hasPayeeHold(it) ? 'Reject payment' : 'Reject', allowed: canReject, reason: canReject ? '' : auditor ? readOnly : `Only ${ownerName} or the controller can reject this.` });
    if (role !== 'auditor') out.push({ action: 'ask', label: `Ask ${owner.name}`, allowed: true, reason: '' });
  }
  if (it.kind === 'invoice' && it.state === 'NEEDS_APPROVAL') {
    const can = role === 'accountant' || role === 'controller';
    const rec = it.data.reconciliation as ReconciliationProposal | undefined;
    const label = rec && rec.dispute_cents > 0 ? `Approve short pay of ${(rec.pay_cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })}` : 'Approve invoice';
    out.push({ action: 'approve', label, allowed: can, reason: can ? '' : auditor ? readOnly : 'Invoices are approved by an accountant or the controller.' });
    out.push({ action: 'reject', label: 'Reject invoice', allowed: can, reason: can ? '' : auditor ? readOnly : 'Invoices are approved by an accountant or the controller.' });
  }
  if (it.kind === 'correction' && it.state === 'PROPOSED') {
    const self = it.proposed_by === actorId;
    const can = (role === 'controller' || role === 'accountant') && !self;
    out.push({ action: 'approve', label: 'Approve correction', allowed: can, reason: can ? '' : self ? 'You proposed this item, so someone else has to approve it.' : auditor ? readOnly : 'Corrections are approved by the controller or an accountant.' });
    out.push({ action: 'reject', label: 'Reject correction', allowed: can, reason: can ? '' : self ? 'You proposed this item, so someone else has to decide it.' : auditor ? readOnly : 'Corrections are approved by the controller or an accountant.' });
  }
  if ((it.kind === 'policy' || it.kind === 'accrual') && it.state === 'PROPOSED') {
    const can = role === 'controller';
    const what = it.kind === 'policy' ? 'Learned policies need the controller.' : 'Accruals are approved by the controller.';
    out.push({ action: 'approve', label: it.kind === 'policy' ? 'Approve policy' : 'Approve accrual', allowed: can, reason: can ? '' : auditor ? readOnly : what });
    out.push({ action: 'reject', label: it.kind === 'policy' ? 'Reject policy' : 'Reject accrual', allowed: can, reason: can ? '' : auditor ? readOnly : what });
  }
  return out;
}

function detail(it: WorkItem, actorId: string): WorkItemDetail {
  const events = audit.filter((e) => e.subject === it.id || (it.kind === 'policy' && e.subject === it.data.policy_id) || it.journal_entry_ids.includes(e.subject));
  const jes = ledger.filter((j) => it.journal_entry_ids.includes(j.id) || (it.kind === 'correction' && j.id === it.data.je_id));
  const linkedIds = new Set(it.links.map((l) => l.id));
  for (const i of items) if (i.links.some((l) => l.id === it.id)) linkedIds.add(i.id);
  const linked = items.filter((i) => linkedIds.has(i.id));
  return {
    item: it,
    events,
    journal_entries: jes,
    linked,
    explanation: explain(it),
    reconciliation: (it.data.reconciliation as ReconciliationProposal | undefined) ?? null,
    next_actions: nextActions(actorId, it),
  };
}

function explain(it: WorkItem): string {
  if (it.kind === 'purchase' && hasPayeeHold(it) && it.state === 'NEEDS_APPROVAL') {
    const dev = it.approvals.find((a) => a.kind === 'spend');
    const pay = !payeePending(it);
    if (dev && !pay) return `${name(dev.actor)} approved the spend. Elix is still holding the money until Priya Raman verifies Stormvault's new payout address, because it changed 1 hour 12 minutes before the request.`;
  }
  if (it.kind === 'correction') return String(it.data.summary ?? `Moves ${it.data.je_id} from ${it.data.from_account} to ${it.data.to_account}.`);
  if (it.kind === 'policy') {
    const p = policies.find((x) => x.id === it.data.policy_id);
    return p ? p.rationale : '';
  }
  if (it.kind === 'purchase' && (it.state === 'POSTED' || it.state === 'PAID') && it.receipt) {
    const je = it.journal_entry_ids[it.journal_entry_ids.length - 1];
    const vendor = world.vendors.find((v) => v.id === it.vendor_id)?.name ?? 'the vendor';
    const who = it.approved_by === 'system:policy' ? 'Elix auto approved it because every check passed and a policy covers it.' : `${name(it.approved_by ?? '')} approved it after Elix sent it for review.`;
    return `${who} The gateway paid ${(it.amount_cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2 })} USDC to ${vendor} over x402 with a signed authorization and posted ${je}.`;
  }
  if (it.kind === 'invoice' && it.state === 'POSTED') {
    const rec = it.data.reconciliation as ReconciliationProposal | undefined;
    return rec && rec.dispute_cents ? `Paid ${fmt(rec.pay_cents)} by ACH and disputed ${fmt(rec.dispute_cents)}, which sits in 2150 Vendor disputes until Lumen sends a credit note.` : `Matched the meter at contract rates. ${name(it.approved_by ?? '')} approved it and the gateway paid by ACH.`;
  }
  return it.decision?.summary ?? '';
}

function summary(period: string): Summary {
  const inP = items.filter((i) => i.period === period);
  const purchases = inP.filter((i) => i.kind === 'purchase');
  const paid = (i: WorkItem) => i.state === 'POSTED' || i.state === 'PAID';
  const spendByAgent = world.agents.map((a) => {
    const mine = purchases.filter((i) => i.requested_by === a.actor_id);
    return {
      agent_id: a.actor_id,
      spent_cents: mine.filter(paid).reduce((s, i) => s + i.amount_cents, 0),
      committed_cents: mine.filter((i) => ['NEEDS_APPROVAL', 'NEEDS_INFO', 'APPROVED', 'PROPOSED'].includes(i.state)).reduce((s, i) => s + i.amount_cents, 0),
      budget_cents: a.monthly_budget_cents,
      cap_cents: a.per_txn_cap_cents,
    };
  });
  const x402 = purchases.filter((i) => paid(i) && i.receipt?.rail === 'x402');
  const bankItems = inP.filter((i) => i.kind === 'invoice' && paid(i));
  // invoices paid in this period, including last month's paid now
  const paidInPeriod = items.filter((i) => i.kind === 'invoice' && paid(i) && i.receipt && i.receipt.settled_at.startsWith(period));
  const days: Summary['daily_spend'] = [];
  const [y, m] = period.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const todayDay = world.company.today.startsWith(period) ? Number(world.company.today.slice(8, 10)) : last;
  for (let d = 1; d <= Math.min(last, todayDay); d++) {
    const ds = `${period}-${String(d).padStart(2, '0')}`;
    days.push({
      date: ds,
      x402_cents: x402.filter((i) => i.receipt!.settled_at.startsWith(ds)).reduce((s, i) => s + i.amount_cents, 0),
      bank_cents: paidInPeriod.filter((i) => i.receipt!.settled_at.startsWith(ds)).reduce((s, i) => s + (i.receipt!.amount_cents), 0),
    });
  }
  const byAccount = new Map<string, number>();
  for (const j of ledger.filter((j) => j.period === period)) {
    for (const l of j.lines) {
      const acct = world.accounts.find((a) => a.code === l.account);
      if (acct?.type !== 'expense') continue;
      byAccount.set(l.account, (byAccount.get(l.account) ?? 0) + l.debit_cents - l.credit_cents);
    }
  }
  const auto = purchases.filter((i) => i.approved_by === 'system:policy').length;
  const human = purchases.filter((i) => i.approved_by && i.approved_by.startsWith('human:')).length;
  const decided = purchases.filter((i) => i.state !== 'NEEDS_APPROVAL' && i.state !== 'NEEDS_INFO' && i.state !== 'PROPOSED' && i.state !== 'NEW');
  const inbox: Record<string, number> = {};
  for (const h of ['human:priya', 'human:dev', 'human:maya', 'human:sam']) inbox[h] = items.filter((i) => inboxFor(h, i)).length;
  return {
    period,
    today: world.company.today.slice(0, 10),
    spend_cents: x402.reduce((s, i) => s + i.amount_cents, 0) + paidInPeriod.reduce((s, i) => s + i.receipt!.amount_cents, 0),
    spend_by_agent: spendByAgent,
    spend_by_rail: [
      { rail: 'x402', cents: x402.reduce((s, i) => s + i.amount_cents, 0), count: x402.length },
      { rail: 'bank', cents: paidInPeriod.reduce((s, i) => s + i.receipt!.amount_cents, 0), count: paidInPeriod.length },
    ],
    spend_by_account: [...byAccount.entries()].filter(([, c]) => c > 0).map(([account, cents]) => ({ account, name: world.accounts.find((a) => a.code === account)!.name, cents })).sort((a, b) => b.cents - a.cents),
    decisions: {
      auto_approved: auto,
      human_approved: human,
      rejected: purchases.filter((i) => i.state === 'REJECTED').length,
      denied: purchases.filter((i) => i.state === 'DENIED').length,
      pending: purchases.filter((i) => i.state === 'NEEDS_APPROVAL' || i.state === 'NEEDS_INFO').length,
    },
    automated_share: decided.length ? auto / decided.length : 0,
    inbox_counts: inbox,
    flags_recent: inP.filter((i) => i.flags.length).slice(-8).reverse().map((i) => ({ item_id: i.id, flag: i.flags[0], at: i.updated_at })),
    daily_spend: days,
    purchases_total: purchases.length,
  };
  void bankItems;
}

function balances(period: string) {
  const rows = world.accounts.map((a) => {
    let d = 0, c = 0;
    for (const j of ledger.filter((j) => j.period <= period)) for (const l of j.lines) if (l.account === a.code) { d += l.debit_cents; c += l.credit_cents; }
    const bal = a.type === 'asset' || a.type === 'expense' ? d - c : c - d;
    return { account: a.code, name: a.name, type: a.type, debit_cents: d, credit_cents: c, balance_cents: bal };
  }).filter((r) => r.debit_cents || r.credit_cents);
  const td = rows.reduce((s, r) => s + r.debit_cents, 0);
  const tc = rows.reduce((s, r) => s + r.credit_cents, 0);
  return { balances: rows, trial_balance_ok: td === tc };
}

function why(subject: string): WhyReport {
  let it = items.find((i) => i.id === subject || (i.kind === 'invoice' && (i.data.invoice as { invoice_number?: string })?.invoice_number === subject));
  if (!it && subject.startsWith('pol-')) it = items.find((i) => i.data.policy_id === subject);
  const subj = it?.id ?? subject;
  const related = new Set<string>([subj]);
  if (it) for (const l of it.links) related.add(l.id);
  if (it) for (const j of it.journal_entry_ids) related.add(j);
  const evs = audit.filter((e) => related.has(e.subject));
  if ((it?.data.invoice as { invoice_number?: string } | undefined)?.invoice_number === 'LUM-2026-10A') {
    const rec = it!.data.reconciliation as ReconciliationProposal;
    const forge = items.find((i) => i.id === it!.links.find((l) => l.rel === 'reconciles')?.id)!;
    const extra = audit.filter((e) => e.subject === forge.id);
    const steps = [...extra, ...evs.filter((e) => e.subject !== forge.id)].sort((a, b) => a.seq - b.seq);
    const paid = it!.state === 'POSTED' || it!.state === 'PAID';
    return {
      subject, title: 'Lumen Compute invoice LUM-2026-10A',
      answer: `${paid ? 'It was paid' : 'Elix proposes paying'} ${fmt(rec.pay_cents)} instead of ${fmt(rec.invoice_total_cents)} because 600 of the 2,140 on demand hours on line L2 were already paid over x402. Forge bought those 600 spot hours on October 9 in ${forge.id} and settled ${fmt(forge.amount_cents)} (${forge.receipt?.tx_ref}). The usage meter counted every hour Forge used, so the invoice billed them twice. Rule R22 caught the overlap and ${paid ? 'Sam Ortiz approved the short pay. The disputed ' + fmt(rec.dispute_cents) + ' sits in 2150 Vendor disputes until Lumen issues a credit note.' : 'the short pay is waiting for Sam Ortiz.'}`,
      steps: steps.map(stepOf), related: [...related].filter((r) => r !== subj),
    };
  }
  if (it?.sku === 'archive-tx-2025') {
    return {
      subject, title: 'Texas 2025 radar archive from Stormvault',
      answer: 'The $4,800.00 archive is on hold because Stormvault\'s payout address changed in its vendor portal at 7:00 AM, 1 hour 12 minutes before Atlas asked for it, and the 402 challenge asks to be paid at the new address. Dev Okafor approved the spend, which covers the over cap amount (R07). Releasing money to a changed payee is a separate decision (R03) that only the controller can make, so the payment waits for Priya Raman to verify the address or reject it.',
      steps: audit.filter((e) => e.subject === it!.id || e.subject === 'ven_stormvault').map(stepOf), related: ['ven_stormvault'],
    };
  }
  if (!it) {
    const evs2 = audit.filter((e) => e.subject === subject);
    if (!evs2.length) throw new Error('nf');
    return { subject, title: subject, answer: `${evs2.length} recorded events for ${subject}. The latest is ${evs2[evs2.length - 1].reason}`, steps: evs2.map(stepOf), related: [] };
  }
  const last = evs[evs.length - 1];
  return { subject, title: it.title, answer: `${it.title} is ${it.state.toLowerCase().replace('_', ' ')}. ${it.decision?.summary ?? ''} ${last ? 'Latest step. ' + last.reason : ''}`.trim(), steps: evs.map(stepOf), related: [...related].filter((r) => r !== subj) };
}
const fmt = (c: number) => (c / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' });
function stepOf(e: AuditEvent) {
  const rules = (e.evidence.rule_ids as string[] | undefined) ?? (e.evidence.rule_id ? [String(e.evidence.rule_id)] : []);
  return { seq: e.seq, at: e.at, actor: e.actor, actor_name: name(e.actor), action: e.action, sentence: e.reason, rule_ids: rules, evidence: e.evidence };
}

// ------------------------------------------------------------ mutations
function setItem(it: WorkItem) {
  items = items.map((i) => (i.id === it.id ? it : i));
  emit({ kind: 'work_item', data: it });
}

function approve(it: WorkItem, actorId: string, note: string) {
  const role = actor(actorId).role;
  const at = tick();
  const n = { ...it, approvals: [...it.approvals], updated_at: at } as WorkItem;
  if (it.kind === 'purchase') {
    n.approvals.push({ actor: actorId, role, at, note, kind: 'spend' });
    n.data = { ...n.data, spend_approved: true };
    appendEvent({ at, actor: actorId, action: 'approve', subject: it.id, allowed: true, reason: `${name(actorId)} approved ${fmt(it.amount_cents)} for ${name(it.requested_by)}.${note ? ' ' + note : ''}`, diff: { approvals: [it.approvals.length, n.approvals.length] }, evidence: {} });
    if (payeePending(n)) {
      setItem(n);
      return n;
    }
    n.state = 'APPROVED';
    n.approved_by = actorId;
    setItem(n);
    return settle(n);
  }
  if (it.kind === 'invoice') {
    const rec = it.data.reconciliation as ReconciliationProposal;
    n.state = 'APPROVED';
    n.approved_by = actorId;
    n.approvals.push({ actor: actorId, role, at, note, kind: 'invoice' });
    appendEvent({ at, actor: actorId, action: 'approve', subject: it.id, allowed: true, reason: rec.dispute_cents ? `${name(actorId)} approved paying ${fmt(rec.pay_cents)} and disputing ${fmt(rec.dispute_cents)}.` : `${name(actorId)} approved invoice ${rec.invoice_number}.`, diff: { state: ['NEEDS_APPROVAL', 'APPROVED'] }, evidence: {} });
    const d = at.slice(0, 10);
    const lines = (it.data.invoice as { lines: { amount_cents: number; description: string }[] }).lines;
    const je1 = addJE(d, `Lumen Compute invoice ${rec.invoice_number}, ${fmt(rec.dispute_cents)} disputed`, [
      { account: '5110', debit_cents: lines[0].amount_cents, credit_cents: 0, cost_center: 'ML', vendor_id: it.vendor_id, memo: lines[0].description },
      { account: '5110', debit_cents: lines[1].amount_cents - rec.dispute_cents, credit_cents: 0, cost_center: 'ML', vendor_id: it.vendor_id, memo: 'On demand H100 hours, undisputed' },
      { account: '2150', debit_cents: rec.dispute_cents, credit_cents: 0, cost_center: null, vendor_id: it.vendor_id, memo: 'Hours already paid over x402' },
      { account: '2010', debit_cents: 0, credit_cents: rec.invoice_total_cents, cost_center: null, vendor_id: it.vendor_id, memo: null },
    ], it.id, at);
    const payAt = tick(4);
    const je2 = addJE(d, `Payment of ${rec.invoice_number}, undisputed amount`, [
      { account: '2010', debit_cents: rec.pay_cents, credit_cents: 0, cost_center: null, vendor_id: it.vendor_id, memo: null },
      { account: '1010', debit_cents: 0, credit_cents: rec.pay_cents, cost_center: null, vendor_id: it.vendor_id, memo: null },
    ], it.id, payAt);
    n.receipt = { rail: 'bank', simulated: true, network: null, tx_ref: 'ach-' + fakeHash(it.id).slice(0, 10), payer: 'Kestrel Labs operating account ending 4410', payee: 'Lumen Compute account ending 7781', amount_cents: rec.pay_cents, amount_atomic: null, payload_sha256: null, signature_verified: null, settled_at: payAt, resource_delivered: false, resource_sha256: null };
    appendEvent({ at: payAt, actor: 'system:gateway', action: 'pay', subject: it.id, allowed: true, reason: `Paid ${fmt(rec.pay_cents)} to Lumen Compute by ACH. Settlement ${n.receipt.tx_ref} is simulated.`, diff: { state: ['APPROVED', 'PAID'] }, evidence: { tx_ref: n.receipt.tx_ref } });
    n.journal_entry_ids = [je1.id, je2.id];
    n.state = 'POSTED';
    appendEvent({ at: payAt, actor: 'system:gateway', action: 'post', subject: it.id, allowed: true, reason: `Posted ${je1.id} with ${fmt(rec.dispute_cents)} in 2150 Vendor disputes, and ${je2.id} for the payment.`, diff: { state: ['PAID', 'POSTED'] }, evidence: { je_ids: [je1.id, je2.id] } });
    setItem(n);
    return n;
  }
  if (it.kind === 'policy') {
    const p = policies.find((x) => x.id === it.data.policy_id)!;
    approvePolicy(p, actorId, note);
    return items.find((i) => i.id === it.id)!;
  }
  if (it.kind === 'correction') {
    n.state = 'POSTED';
    n.approved_by = actorId;
    n.approvals.push({ actor: actorId, role, at, note, kind: 'correction' });
    const orig = ledger.find((j) => j.id === it.data.je_id)!;
    const d = at.slice(0, 10);
    const rev = addJE(d, `Reversal of ${orig.id}`, orig.lines.map((l) => ({ ...l, debit_cents: l.credit_cents, credit_cents: l.debit_cents })), it.id, at, orig.id);
    orig.reversed_by = rev.id;
    const rep = addJE(d, `Repost of ${orig.id} to 6210 Marketing data`, orig.lines.map((l, i) => (i === 0 ? { ...l, account: '6210' } : { ...l })), it.id, at);
    n.journal_entry_ids = [rev.id, rep.id];
    appendEvent({ at, actor: actorId, action: 'approve', subject: it.id, allowed: true, reason: `${name(actorId)} approved the correction of ${orig.id}.`, diff: { state: ['PROPOSED', 'APPROVED'] }, evidence: {} });
    appendEvent({ at, actor: 'system:gateway', action: 'post', subject: it.id, allowed: true, reason: `Posted reversal ${rev.id} and repost ${rep.id} in October.`, diff: { state: ['APPROVED', 'POSTED'] }, evidence: {} });
    setItem(n);
    return n;
  }
  return n;
}

function addJE(date: string, memo: string, lines: JournalEntry['lines'], source: string, at: string, reverses: string | null = null) {
  const p = date.slice(0, 7);
  const n = ledger.filter((j) => j.period === p).length + 1;
  const je: JournalEntry = { id: `JE-${p}-${String(n).padStart(4, '0')}`, date, period: p, memo, lines, source_work_item: source, reverses, reversed_by: null, posted_by: 'system:gateway', created_at: at };
  ledger.push(je);
  emit({ kind: 'journal', data: je });
  return je;
}

function settle(it: WorkItem): WorkItem {
  const at = tick(2);
  const n = { ...it } as WorkItem;
  const payload = fakeHash('payload' + it.id);
  if (it.challenge) {
    n.receipt = { rail: 'x402', simulated: true, network: it.challenge.network, tx_ref: 'sim-' + payload.slice(0, 16), payer: '0x5A0b54D5dc17e0AadC383d2db43B0a0D3E029c4c', payee: it.challenge.pay_to, amount_cents: it.amount_cents, amount_atomic: it.challenge.amount_atomic, payload_sha256: payload, signature_verified: true, settled_at: at, resource_delivered: true, resource_sha256: fakeHash('res' + it.id) };
  }
  appendEvent({ at, actor: 'system:gateway', action: 'pay', subject: it.id, allowed: true, reason: `Paid ${fmt(it.amount_cents)} to ${world.vendors.find((v) => v.id === it.vendor_id)?.name} over x402. Settlement ${n.receipt?.tx_ref} is simulated.`, diff: { state: ['APPROVED', 'PAID'] }, evidence: {} });
  const je = addJE(at.slice(0, 10), `${it.title} for ${name(it.requested_by)}`, [
    { account: it.account ?? '6110', debit_cents: it.amount_cents, credit_cents: 0, cost_center: it.cost_center ?? null, vendor_id: it.vendor_id ?? null, memo: null },
    { account: '1015', debit_cents: 0, credit_cents: it.amount_cents, cost_center: null, vendor_id: it.vendor_id ?? null, memo: null },
  ], it.id, at);
  n.journal_entry_ids = [...it.journal_entry_ids, je.id];
  n.state = 'POSTED';
  n.updated_at = at;
  appendEvent({ at, actor: 'system:gateway', action: 'post', subject: it.id, allowed: true, reason: `Posted ${je.id}. Debit ${it.account}, credit 1015 USDC wallet.`, diff: { state: ['PAID', 'POSTED'] }, evidence: {} });
  setItem(n);
  return n;
}

function approvePolicy(p: Policy, actorId: string, note: string) {
  const at = tick();
  p.status = 'active';
  p.approved_by = actorId;
  p.activated_at = at;
  appendEvent({ at, actor: actorId, action: 'approve', subject: p.id, allowed: true, reason: `${name(actorId)} approved ${p.id}. ${p.rationale.split('.')[0]}.${note ? ' ' + note : ''}`, diff: { status: ['proposed', 'active'] }, evidence: {} });
  const wi = items.find((i) => i.data.policy_id === p.id);
  if (wi) setItem({ ...wi, state: 'APPROVED', approved_by: actorId, updated_at: at });
  emit({ kind: 'policy', data: p });
}

// ------------------------------------------------------------ router
export async function handle(method: string, fullPath: string, body: unknown, token: string): Promise<{ status: number; body: unknown }> {
  await delay(method === 'GET' ? 60 : 180);
  const actorId = TOKENS[token];
  if (!actorId) return err(401, 'unauthorized', 'Unknown token.');
  const url = new URL(fullPath, 'http://x');
  const p = url.pathname;
  const q = url.searchParams;
  const b = (body ?? {}) as Record<string, unknown>;
  let m: RegExpMatchArray | null;

  if (method === 'GET') {
    if (p === '/api/session') {
      const a = actor(actorId);
      return ok({ actor: a, role: a.role, can: { approve_purchases: a.role === 'budget_owner' || a.role === 'controller', approve_invoices: a.role === 'accountant' || a.role === 'controller', approve_policies: a.role === 'controller', verify_payee: a.role === 'controller', recode: a.role === 'accountant' || a.role === 'controller', close_period: a.role === 'controller', run_scenarios: a.kind === 'human' } });
    }
    if (p === '/api/world') return ok(world);
    if (p === '/api/summary') return ok(summary(q.get('period') ?? '2026-10'));
    if (p === '/api/work-items') {
      let list = items.slice();
      const period = q.get('period');
      if (period) list = list.filter((i) => i.period === period);
      for (const k of ['state', 'kind'] as const) { const v = q.get(k); if (v) list = list.filter((i) => v.split(',').includes(i[k])); }
      const agent = q.get('agent'); if (agent) list = list.filter((i) => i.requested_by === agent || i.requested_by === 'agent:' + agent);
      const vendor = q.get('vendor'); if (vendor) list = list.filter((i) => i.vendor_id === vendor);
      const flag = q.get('flag'); if (flag) list = list.filter((i) => i.flags.includes(flag as never));
      if (q.get('inbox') === 'me') list = list.filter((i) => inboxFor(actorId, i)).sort((a, z) => (a.created_at < z.created_at ? -1 : 1));
      else list.sort((a, z) => (a.updated_at < z.updated_at ? 1 : -1));
      const total = list.length;
      const off = Number(q.get('offset') ?? 0); const lim = Number(q.get('limit') ?? 100);
      return ok({ items: list.slice(off, off + lim), total });
    }
    if ((m = p.match(/^\/api\/work-items\/([^/]+)\/x402$/))) {
      const it = items.find((i) => i.id === m![1]);
      if (!it || !it.challenge) return err(404, 'not_found', 'No x402 exchange for this item.');
      const raw = JSON.parse(atob(it.challenge.raw_b64));
      return ok({ challenge_decoded: raw, payload_sha256: it.receipt?.payload_sha256 ?? null, payment_response_decoded: it.receipt ? { success: true, transaction: it.receipt.tx_ref, network: it.receipt.network, payer: it.receipt.payer } : null, signature_verified: it.receipt?.signature_verified ?? null, simulated: true });
    }
    if ((m = p.match(/^\/api\/work-items\/([^/]+)$/))) {
      const it = items.find((i) => i.id === m![1]);
      if (!it) return err(404, 'not_found', `No work item ${m[1]}.`);
      return ok(detail(it, actorId));
    }
    if (p === '/api/reconciliations') {
      const period = q.get('period');
      const inv = items.filter((i) => i.kind === 'invoice' && (!period || i.period === period || (period === '2026-10' && i.created_at.startsWith('2026-10'))));
      inv.sort((a, z) => (a.created_at < z.created_at ? 1 : -1));
      return ok({ items: inv.map((i) => ({ item: i, reconciliation: i.data.reconciliation })) });
    }
    if (p === '/api/ledger') {
      let list = ledger.filter((j) => !q.get('period') || j.period === q.get('period'));
      const acct = q.get('account'); if (acct) list = list.filter((j) => j.lines.some((l) => l.account === acct));
      list = list.slice().sort((a, z) => (a.created_at < z.created_at ? 1 : a.created_at > z.created_at ? -1 : a.id < z.id ? 1 : -1));
      const off = Number(q.get('offset') ?? 0); const lim = Number(q.get('limit') ?? 200);
      return ok({ entries: list.slice(off, off + lim), total: list.length });
    }
    if (p === '/api/balances') return ok(balances(q.get('period') ?? '2026-10'));
    if (p === '/api/policies') { const s = q.get('status'); return ok({ policies: policies.filter((x) => !s || x.status === s) }); }
    if (p === '/api/periods') return ok({ periods: world.periods });
    if (p === '/api/audit/verify') return ok({ ok: true, checked: audit.length, first_bad_seq: null, head_hash: audit[audit.length - 1].hash });
    if (p === '/api/audit') {
      let list = audit.slice();
      const subj = q.get('subject'); if (subj) list = list.filter((e) => e.subject === subj);
      const act = q.get('actor'); if (act) list = list.filter((e) => e.actor === act);
      const action = q.get('action'); if (action) list = list.filter((e) => e.action === action);
      const allowed = q.get('allowed'); if (allowed) list = list.filter((e) => String(e.allowed) === allowed);
      const before = q.get('before_seq'); if (before) list = list.filter((e) => e.seq < Number(before));
      list.reverse();
      return ok({ events: list.slice(0, Number(q.get('limit') ?? 200)) });
    }
    if ((m = p.match(/^\/api\/why\/(.+)$/))) {
      try { return ok(why(decodeURIComponent(m[1]))); } catch { return err(404, 'not_found', `Elix has no record of ${decodeURIComponent(m[1])}.`); }
    }
    if (p === '/api/agents') {
      const s = summary('2026-10');
      return ok({ agents: world.agents.map((a) => { const sp = s.spend_by_agent.find((x) => x.agent_id === a.actor_id)!; const last = items.filter((i) => i.requested_by === a.actor_id).slice(-1)[0]; return { ...a, spent_cents: sp.spent_cents, committed_cents: sp.committed_cents, last_activity_at: last?.created_at ?? null, mode: 'scripted' }; }) });
    }
    if (p === '/api/agents/sessions') { const a = q.get('agent'); return ok({ sessions: sessions.filter((s) => !a || s.agent_id === a || s.agent_id === 'agent:' + a).map(({ transcript: _t, ...s }) => s) }); }
    if ((m = p.match(/^\/api\/agents\/sessions\/(.+)$/))) { const s = sessions.find((x) => x.id === m![1]); return s ? ok(s) : err(404, 'not_found', 'No such session.'); }
    if (p === '/api/eval/latest') return ok(evalJ);
    if (p === '/api/scenarios') return ok({ scenarios });
    if (p === '/healthz') return ok({ ok: true });
  }

  if (method === 'POST') {
    const role = actor(actorId).role;
    if ((m = p.match(/^\/api\/work-items\/([^/]+)\/(approve|reject|verify-payee|ask)$/))) {
      const it = items.find((i) => i.id === m![1]);
      if (!it) return err(404, 'not_found', `No work item ${m[1]}.`);
      const act = m[2] === 'verify-payee' ? 'verify_payee' : m[2];
      const na = nextActions(actorId, it).find((x) => x.action === act);
      if (!na || !na.allowed) {
        const e = appendEvent({ at: tick(), actor: actorId, action: 'denied:' + act, subject: it.id, allowed: false, reason: `${name(actorId)} tried to ${act.replace('_', ' ')} ${it.id}. Refused. ${na?.reason ?? 'That action is not available in this state.'}`, diff: {}, evidence: { rule_id: 'R31' } });
        return err(403, 'denied', na?.reason ?? 'That action is not available in this state.', 'R31', e.seq);
      }
      if (act === 'approve') { approve(it, actorId, String(b.note ?? '')); }
      if (act === 'reject') {
        const at = tick();
        const n = { ...it, state: 'REJECTED', updated_at: at } as WorkItem;
        appendEvent({ at, actor: actorId, action: 'reject', subject: it.id, allowed: true, reason: `${name(actorId)} rejected ${it.id}. ${String(b.note ?? '')}`.trim(), diff: { state: [it.state, 'REJECTED'] }, evidence: {} });
        if (it.kind === 'policy') { const pol = policies.find((x) => x.id === it.data.policy_id); if (pol) pol.status = 'rejected'; }
        setItem(n);
      }
      if (act === 'verify_payee') {
        const at = tick();
        const n = { ...it, approvals: [...it.approvals, { actor: actorId, role, at, note: `${String(b.method ?? '')}. ${String(b.note ?? '')}`.trim(), kind: 'payee' }], data: { ...it.data, payee_verified: true }, updated_at: at } as WorkItem;
        appendEvent({ at, actor: actorId, action: 'verify_payee', subject: it.id, allowed: true, reason: `${name(actorId)} verified Stormvault's new payout address. ${String(b.method ?? '')}.`, diff: { payee_hold: [true, false] }, evidence: { method: b.method } });
        if (spendApproved(n)) { n.state = 'APPROVED'; n.approved_by = actorId; setItem(n); settle(n); } else setItem(n);
      }
      if (act === 'ask') {
        const at = tick();
        const n = { ...it, questions: [...it.questions, { id: 'q-' + at, asked_by: actorId, to: it.requested_by, text: String(b.text ?? ''), answer: null, answered_by: null, at, answered_at: null }], updated_at: at } as WorkItem;
        appendEvent({ at, actor: actorId, action: 'ask', subject: it.id, allowed: true, reason: `${name(actorId)} asked ${name(it.requested_by)} a question.`, diff: {}, evidence: {} });
        setItem(n);
      }
      return ok(detail(items.find((i) => i.id === it.id)!, actorId));
    }
    if ((m = p.match(/^\/api\/policies\/([^/]+)\/(approve|reject)$/))) {
      const pol = policies.find((x) => x.id === m![1]);
      if (!pol) return err(404, 'not_found', 'No such policy.');
      if (role !== 'controller') {
        const e = appendEvent({ at: tick(), actor: actorId, action: 'denied:approve', subject: pol.id, allowed: false, reason: `${name(actorId)} tried to ${m[2]} ${pol.id}. Refused, because learned policies need the controller.`, diff: {}, evidence: { rule_id: 'R31' } });
        return err(403, 'denied', 'Learned policies need the controller.', 'R31', e.seq);
      }
      if (m[2] === 'approve') approvePolicy(pol, actorId, String(b.note ?? ''));
      else { pol.status = 'rejected'; appendEvent({ at: tick(), actor: actorId, action: 'reject', subject: pol.id, allowed: true, reason: `${name(actorId)} rejected ${pol.id}.`, diff: { status: ['proposed', 'rejected'] }, evidence: {} }); const wi = items.find((i) => i.data.policy_id === pol.id); if (wi) setItem({ ...wi, state: 'REJECTED' }); }
      return ok(pol);
    }
    if (p === '/api/policies/learn') {
      appendEvent({ at: tick(), actor: 'system:learner', action: 'learn', subject: 'learner', allowed: true, reason: `Elix learner reviewed July to September again. No new proposals, since the open ones already cover every qualifying group. Triggered by ${name(actorId)}.`, diff: {}, evidence: { triggered_by: actorId } });
      return ok({ proposed: [] });
    }
    if ((m = p.match(/^\/api\/ledger\/([^/]+)\/recode$/))) {
      const je = ledger.find((j) => j.id === m![1]);
      if (!je) return err(404, 'not_found', 'No such entry.');
      if (role !== 'accountant' && role !== 'controller') return err(403, 'denied', 'Recoding needs an accountant or the controller.', 'R31');
      const at = tick();
      const id = 'WI-' + String(items.length + 1).padStart(4, '0');
      const closed = world.periods.find((x) => x.id === je.period)?.status === 'closed';
      const wi: WorkItem = { ...items.find((i) => i.kind === 'correction')!, id, state: 'PROPOSED', period: '2026-10', created_at: at, updated_at: at, requested_by: actorId, proposed_by: actorId, approved_by: null, approvals: [], journal_entry_ids: [], title: `Recode ${je.id} line ${Number(b.line_index) + 1} to ${b.new_account}`, data: { je_id: je.id, line_index: b.line_index, from_account: je.lines[Number(b.line_index)].account, to_account: b.new_account, note: b.note, closed_period: closed, summary: closed ? `${je.period} is closed, so the fix posts in October as a reversal and a repost.` : 'Posts a reversal and a corrected repost today.' }, links: [{ rel: 'corrects', id: je.id }] };
      items = [...items, wi];
      if (closed) appendEvent({ at, actor: actorId, action: 'denied:edit', subject: je.id, allowed: false, reason: `Refused to edit ${je.id} in place because ${je.period} is closed. Elix created ${id} to fix it in October.`, diff: {}, evidence: { rule_id: 'R30' } });
      appendEvent({ at, actor: actorId, action: 'recode', subject: id, allowed: true, reason: `${name(actorId)} proposed moving ${je.id} line ${Number(b.line_index) + 1} to ${b.new_account}.`, diff: {}, evidence: {} });
      emit({ kind: 'work_item', data: wi });
      return ok(detail(wi, actorId));
    }
    if ((m = p.match(/^\/api\/periods\/([^/]+)\/close-preview$/))) {
      return ok({ period: m[1], accruals: items.filter((i) => i.kind === 'accrual').slice(-3), blockers: items.filter((i) => i.period === m![1] && ['NEEDS_APPROVAL', 'NEEDS_INFO'].includes(i.state)).map((i) => ({ item_id: i.id, reason: `${i.title} is waiting for a decision.` })), can_close: false });
    }
    if ((m = p.match(/^\/api\/scenarios\/([^/]+)\/run$/))) {
      const s = scenarios.find((x) => x.id === m![1]);
      if (!s) return err(404, 'not_found', 'No such scenario.');
      await delay(500);
      const ids = s.last_run?.result_ids ?? [];
      emit({ kind: 'scenario', data: { id: s.id, status: 'done', message: 'Fixture mode replays the recorded result.' } });
      return ok({ scenario_id: s.id, result_ids: ids, message: ids.length ? `Fixture mode shows the recorded run. ${ids.join(', ')} ${ids.length === 1 ? 'is' : 'are'} ready.` : 'Fixture mode cannot run this scenario. Start the API to run it for real.' });
    }
    if ((m = p.match(/^\/api\/agents\/([^/]+)\/run$/))) return ok({ session_id: sessions.find((s) => s.agent_id.endsWith(m![1]))?.id ?? sessions[0].id });
    if (p === '/api/eval/run') return ok({ started: true });
    if (p === '/api/demo/reset') { await delay(800); return ok({ ok: true }); }
  }
  return err(404, 'not_found', `Fixture mode has no route for ${method} ${p}.`);
}
