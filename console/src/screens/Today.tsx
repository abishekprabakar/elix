import { Link } from 'react-router-dom';
import type { Summary, WorkItemList } from '../api/types';
import { Avatar, ErrorState, Flags, Money, Skeleton } from '../components/Bits';
import { disputeOf } from '../components/CreditNotes';
import { ElixirMeter } from '../components/Meter';
import { Pill } from '../components/Pill';
import { age, longDate, money, moneyWhole, periodMonth, plural } from '../lib/format';
import { useApi, useStore } from '../lib/store';
import { useDrawer } from '../shell/Drawer';

export function Today() {
  const { period, world, session, now } = useStore();
  const { data: s, error } = useApi<Summary>(period ? `/api/summary?period=${period}` : null);
  const { data: inbox } = useApi<WorkItemList>(session ? '/api/work-items?inbox=me&limit=50' : null);
  const isOpen = world?.periods.find((p) => p.id === period)?.status === 'open';
  const { liveEvents } = useStore();
  const { data: items } = useApi<WorkItemList>(period ? `/api/work-items?period=${period}&limit=1000&v=${liveEvents.length}` : null);
  const { data: waiting } = useApi<WorkItemList>(`/api/work-items?state=NEEDS_APPROVAL&limit=300&v=${liveEvents.length}`);

  return (
    <div className="page page-today">
      <div className="today-main">
        <header className="today-head">
          <p className="today-date ink-2">
            {world ? longDate(world.company.today) : ''}
            {!isOpen && period && <span>, viewing {periodMonth(period)}</span>}
          </p>
          <h1 className="page-title">Today</h1>
        </header>
        <Kpis s={s} inbox={inbox?.total ?? 0} month={periodMonth(period)} held={items ? heldBack(items.items) : null} credited={items ? creditedBack(items.items) : 0} />
        <ErrorState error={error} what="the summary" />
        <AgentsStrip s={s} />
        <NeedsYou items={inbox?.items ?? null} total={inbox?.total ?? 0} s={s} now={now} />
        <WaitingOn items={waiting?.items ?? null} now={now} />
        <Vendors items={items?.items ?? null} s={s} />
      </div>
      <LiveDecisions />
    </div>
  );
}

function heldNote(held: number | null, credited: number): string {
  if (!held) return 'Nothing billed twice this month';
  if (!credited) return 'Billed twice or over the meter';
  return credited >= held ? `${money(credited)} credited back by the vendor` : `${money(credited)} credited back, ${money(held - credited)} still in dispute`;
}

function Kpis({ s, inbox, month, held, credited }: { s: Summary | null; inbox: number; month: string; held: number | null; credited: number }) {
  const x402 = s?.spend_by_rail.find((r) => r.rail === 'x402');
  const bank = s?.spend_by_rail.find((r) => r.rail === 'bank');
  const cells: { label: string; value: string; note?: string }[] = [
    { label: `Paid over x402 in ${month}`, value: x402 ? money(x402.cents) : '$0.00', note: x402 ? `${x402.count} payments signed by the gateway` : undefined },
    { label: 'Paid on vendor invoices', value: bank ? money(bank.cents) : '$0.00', note: bank ? `${bank.count} invoices, matched line by line` : undefined },
    { label: 'Held back from vendors', value: held != null ? money(held) : '', note: heldNote(held, credited) },
    { label: 'Waiting for you', value: String(inbox) },
  ];
  return (
    <dl className="kpis">
      {cells.map((c) => (
        <div key={c.label} className="kpi">
          <dt className="ink-2">{c.label}</dt>
          <dd className="tnum">{s ? c.value : <span className="skeleton-bar" style={{ width: 80, height: 24, display: 'block' }} />}</dd>
          {c.note && <dd className="kpi-note ink-3">{c.note}</dd>}
        </div>
      ))}
    </dl>
  );
}

function AgentsStrip({ s }: { s: Summary | null }) {
  const { world, actorName } = useStore();
  if (!world) return null;
  return (
    <section className="agents-strip" aria-label="Agent budgets">
      {world.agents.map((a) => {
        const sp = s?.spend_by_agent.find((x) => x.agent_id === a.actor_id);
        return (
          <Link key={a.actor_id} to={`/agents?agent=${a.actor_id.replace('agent:', '')}`} className="agent-cell">
            <div className="agent-cell-head">
              <Avatar id={a.actor_id} size={20} />
              <span className="agent-cell-name">{a.name}</span>
              <span className="agent-cell-owner ink-3">
                {a.cost_center}, {actorName(a.owner_id)}
              </span>
            </div>
            {sp ? <ElixirMeter spent={sp.spent_cents} committed={sp.committed_cents} budget={sp.budget_cents} cap={sp.cap_cents} /> : <span className="skeleton-bar" style={{ height: 22, width: '100%' }} />}
            <p className="agent-cell-cap ink-3 tnum">
              Cap {moneyWhole(a.per_txn_cap_cents)} a purchase, auto under {moneyWhole(a.auto_approve_under_cents)}
            </p>
          </Link>
        );
      })}
    </section>
  );
}

function NeedsYou({ items, total, s, now }: { items: WorkItemList['items'] | null; total: number; s: Summary | null; now: string }) {
  const { open } = useDrawer();
  const { world, actorName } = useStore();
  if (items && items.length === 0) return null;
  return (
    <section className="section needs">
      <div className="section-head">
        <h2 className="section-title">
          Needs you {total > 0 && <span className="count tnum">{total}</span>}
        </h2>
        {total > 0 && (
          <Link to="/inbox" className="section-link">
            Open inbox
          </Link>
        )}
      </div>
      {!items ? (
        <Skeleton rows={4} cols={4} className="panel" />
      ) : items.length === 0 ? (
        <div className="panel needs-empty">
          <p className="needs-empty-title">Nothing needs you right now.</p>
          {s && (
            <p className="ink-2 tnum">
              This month Elix approved {plural(s.decisions.auto_approved, 'purchase')} on its own, people approved {s.decisions.human_approved}, and {plural(s.decisions.denied, 'request was', 'requests were')} denied before any money moved.
            </p>
          )}
        </div>
      ) : (
        <ul className="panel needs-list">
          {items.slice(0, 6).map((it) => {
            const agent = world?.agents.find((a) => a.actor_id === it.requested_by);
            const vendor = world?.vendors.find((v) => v.id === it.vendor_id);
            return (
              <li key={it.id}>
                <button type="button" className="needs-row" onClick={() => open(it.id)}>
                  <span className="needs-pill">
                    <Pill state={it.state} item={it} />
                  </span>
                  <span className="needs-main">
                    <span className="needs-title truncate">{it.title}</span>
                    <span className="needs-summary">
                      <span className="needs-who">{agent?.name ?? vendor?.name ?? actorName(it.requested_by)}</span>
                      {summaryOf(it, actorName)}
                    </span>
                  </span>
                  <Money cents={it.amount_cents || null} className="needs-amt" />
                  <span className="needs-age ink-3 tnum">{age(it.created_at, now)}</span>
                </button>
              </li>
            );
          })}
          {items.length > 6 && (
            <li className="needs-more">
              <Link to="/inbox">{items.length - 6} more in the inbox</Link>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

function summaryOf(it: WorkItemList['items'][number], actorName: (id?: string | null) => string): string {
  if (it.kind === 'policy') return 'Learned from history. It needs the controller before it can approve anything.';
  if (it.kind === 'correction') return `Recode proposed by ${actorName(it.proposed_by)}. Someone else has to approve it.`;
  if (it.kind === 'accrual') return 'Month end accrual waiting for the controller.';
  return it.decision?.summary ?? '';
}

function heldBack(list: WorkItemList['items']): number {
  return list.filter((it) => it.kind === 'invoice').reduce((t, it) => t + (Number(it.data?.dispute_cents) || 0), 0);
}

/** What vendors credited back against disputes Elix opened this month. */
function creditedBack(list: WorkItemList['items']): number {
  return list.reduce((t, it) => t + (disputeOf(it)?.credited ?? 0), 0);
}

type VendorRow = { id: string; name: string; billing: string; x402: number; x402n: number; invoiced: number; held: number; open: number; inv: WorkItemList['items'] };

function Vendors({ items, s }: { items: WorkItemList['items'] | null; s: Summary | null }) {
  const { world } = useStore();
  const { open } = useDrawer();
  const rows: VendorRow[] = (world?.vendors ?? []).map((v) => {
    const mine = (items ?? []).filter((it) => it.vendor_id === v.id);
    const paid = mine.filter((it) => it.kind === 'purchase' && it.receipt && it.receipt.rail === 'x402' && (it.state === 'POSTED' || it.state === 'PAID'));
    const inv = mine.filter((it) => it.kind === 'invoice');
    return {
      id: v.id,
      name: v.name,
      billing: v.billing,
      x402: paid.reduce((t, it) => t + it.amount_cents, 0),
      x402n: paid.length,
      invoiced: inv.reduce((t, it) => t + it.amount_cents, 0),
      held: inv.reduce((t, it) => t + (Number(it.data?.dispute_cents) || 0), 0),
      open: inv.reduce((t, it) => t + (disputeOf(it)?.open ?? 0), 0),
      inv,
    };
  }).filter((r) => r.x402 > 0 || r.invoiced > 0).sort((a, b) => b.x402 + b.invoiced - (a.x402 + a.invoiced));
  const maxAcct = Math.max(1, ...(s?.spend_by_account ?? []).map((a) => a.cents));
  return (
    <section className="section vendors">
      <div className="section-head">
        <h2 className="section-title">Vendors this month</h2>
        <Link to="/reconcile" className="section-link">Open reconcile</Link>
      </div>
      <p className="vendors-lede ink-2">What agents paid each vendor over x402, what the vendor invoiced, and whether the two agree.</p>
      {!items ? (
        <Skeleton rows={6} cols={5} className="panel" />
      ) : (
        <div className="panel vendors-table-wrap">
          <table className="vendors-table tnum">
            <thead>
              <tr><th>Vendor</th><th className="r">Paid over x402</th><th className="r">Invoiced</th><th className="r">Held back</th><th>Status</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const waitingInv = r.inv.find((it) => it.state === 'NEEDS_APPROVAL' || it.state === 'PROPOSED');
                const status = r.held > 0
                  ? r.open === 0
                    ? <Pill kind="posted" label="Billed twice, credited" />
                    : r.open < r.held
                      ? <Pill kind="needs" label="Billed twice, partly credited" />
                      : <Pill kind="denied" label="Billed twice, held back" />
                  : waitingInv
                    ? <Pill kind="needs" label="Invoice waiting" />
                    : r.inv.length
                      ? <Pill kind="posted" label="Invoice matched" />
                      : r.billing === 'per_request'
                        ? <span className="ink-3">Pays per request, no invoice</span>
                        : <span className="ink-3">Invoice not in yet</span>;
                const target = r.inv.find((it) => (Number(it.data?.dispute_cents) || 0) > 0) ?? waitingInv ?? r.inv[0];
                return (
                  <tr key={r.id} className={target ? 'is-link' : ''} onClick={target ? () => open(target.id) : undefined}>
                    <td className="v-name">{r.name}</td>
                    <td className="r">{r.x402 ? <>{money(r.x402)}<span className="ink-3 v-n"> {r.x402n} paid</span></> : <span className="ink-3">None</span>}</td>
                    <td className="r">{r.invoiced ? money(r.invoiced) : <span className="ink-3">None</span>}</td>
                    <td className={`r${r.open ? ' v-held' : ''}`}>{r.held ? money(r.held) : <span className="ink-3">None</span>}</td>
                    <td>{status}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="acct-list">
        <p className="fig-label">Booked by account</p>
        <ul>
          {(s?.spend_by_account ?? []).slice(0, 5).map((a) => (
            <li key={a.account} className="acct-row">
              <span className="acct-name">
                <span className="acct-code tnum ink-3">{a.account}</span> {a.name}
              </span>
              <span className="acct-track" aria-hidden="true">
                <span className="acct-fill" style={{ width: `${(a.cents / maxAcct) * 100}%` }} />
              </span>
              <span className="num acct-v">{Math.abs(a.cents) < 10000 ? money(a.cents) : moneyWhole(a.cents)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function WaitingOn({ items, now }: { items: WorkItemList['items'] | null; now: string }) {
  const { world, session, actorName } = useStore();
  if (!items || !world) return null;
  const byRole = (role: string) => world.actors.find((a) => a.role === role)?.id;
  const who = (it: WorkItemList['items'][number]): string | undefined => {
    if (it.data?.payee_hold && it.data?.spend_approved) return byRole('controller');
    if (it.required_approver_role === 'budget_owner') return world.agents.find((a) => a.actor_id === it.requested_by)?.owner_id ?? undefined;
    if (it.required_approver_role) return byRole(it.required_approver_role);
    return undefined;
  };
  const groups = new Map<string, { n: number; cents: number; oldest: string }>();
  for (const it of items) {
    const id = who(it);
    if (!id) continue;
    const g = groups.get(id) ?? { n: 0, cents: 0, oldest: it.created_at };
    g.n += 1;
    g.cents += it.amount_cents;
    if (it.created_at < g.oldest) g.oldest = it.created_at;
    groups.set(id, g);
  }
  const rows = [...groups.entries()].sort((a, b) => b[1].cents - a[1].cents);
  if (!rows.length) return null;
  return (
    <section className="section waiting">
      <div className="section-head">
        <h2 className="section-title">Waiting on people</h2>
        <Link to="/inbox" className="section-link">Open inbox</Link>
      </div>
      <ul className="waiting-list">
        {rows.map(([id, g]) => (
          <li key={id} className={id === session?.actor.id ? 'is-me' : ''}>
            <Avatar id={id} size={24} />
            <span className="w-name">{actorName(id)}{id === session?.actor.id ? <span className="ink-3"> (you)</span> : null}</span>
            <span className="ink-2 tnum">{g.n === 1 ? '1 item' : `${g.n} items`}</span>
            <span className="num w-amt">{money(g.cents)}</span>
            <span className="ink-3 tnum w-age">oldest {age(g.oldest, now)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LiveDecisions() {
  const { liveEvents, world, now } = useStore();
  const { open } = useDrawer();
  // Recent purchases and invoices, newest first, one row each. The full trail is in the audit log.
  const { data } = useApi<WorkItemList>(`/api/work-items?limit=40&v=${liveEvents.length}`);
  const raw = (data?.items ?? []).filter((it) => it.kind === 'purchase' || it.kind === 'invoice');
  // fold repeats (the same agent buying the same thing with the same outcome) into one row
  const items: (WorkItemList['items'][number] & { n: number; total: number })[] = [];
  for (const it of raw) {
    const last = items[items.length - 1];
    if (last && last.requested_by === it.requested_by && last.vendor_id === it.vendor_id && last.sku === it.sku && last.state === it.state) {
      last.n += 1;
      last.total += it.amount_cents;
    } else items.push({ ...it, n: 1, total: it.amount_cents });
  }
  return (
    <aside className="today-rail" aria-label="Recent activity">
      <div className="section-head">
        <h2 className="section-title">Recent activity</h2>
        <span className="live-dot" aria-hidden="true" />
      </div>
      {!data ? (
        <Skeleton rows={10} cols={2} />
      ) : (
        <ol className="recent-list">
          {items.slice(0, 12).map((it) => {
            const agent = world?.agents.find((x) => x.actor_id === it.requested_by);
            const vendor = world?.vendors.find((v) => v.id === it.vendor_id);
            return (
              <li key={it.id}>
                <button type="button" className="recent-row" onClick={() => open(it.id)}>
                  <Avatar id={it.requested_by} size={22} />
                  <span className="recent-main">
                    <span className="recent-title truncate">{it.n > 1 ? `${it.n} orders from ${vendor?.name ?? 'one vendor'}` : it.title}</span>
                    <span className="recent-sub ink-3 truncate">
                      {agent?.name ?? vendor?.name ?? ''}
                      {agent && vendor ? `, ${vendor.name}` : ''}
                    </span>
                  </span>
                  <span className="recent-right">
                    <Money cents={it.total || null} className="recent-amt" />
                    <Pill state={it.state} item={it} />
                  </span>
                  <span className="recent-age ink-3 tnum">{age(it.updated_at, now)}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
      <Link to="/audit" className="section-link rail-link">
        Open the audit log
      </Link>
    </aside>
  );
}

// re-exported for the inbox
export { Flags };
