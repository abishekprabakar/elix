import { useEffect, useState } from 'react';
import type { WorkItemList } from '../api/types';
import { ErrorState, Flags, Money, Skeleton } from '../components/Bits';
import { ItemDetail } from '../components/ItemDetail';
import { Pill } from '../components/Pill';
import { age } from '../lib/format';
import { useListKeys } from '../lib/hooks';
import { PEOPLE, useApi, useStore } from '../lib/store';

export function Inbox() {
  const { session, world, now, actorName, token } = useStore();
  const { data, error, loading } = useApi<WorkItemList>(session ? '/api/work-items?inbox=me&limit=100' : null);
  const items = data?.items ?? [];
  const [sel, setSel] = useState<string | null>(() => new URLSearchParams(window.location.search).get('open'));
  const [kIndex, setKIndex] = useListKeys(items.length, (i) => setSel(items[i].id));

  useEffect(() => {
    if (!items.length) return;
    if (!sel || !items.some((i) => i.id === sel)) {
      if (sel && data) return; // keep showing an item that just left the inbox
      setSel(items[0].id);
    }
  }, [items, sel, data]);
  useEffect(() => {
    const i = items.findIndex((x) => x.id === sel);
    if (i >= 0) setKIndex(i);
  }, [sel, items, setKIndex]);
  useEffect(() => {
    if (kIndex >= 0 && items[kIndex] && items[kIndex].id !== sel) setSel(items[kIndex].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kIndex]);
  useEffect(() => setSel(null), [token]);

  const me = PEOPLE.find((p) => p.token === token);

  return (
    <div className="page page-split">
      <div className="split-list">
        <header className="page-head page-head-tight">
          <div>
            <h1 className="page-title">Inbox</h1>
            <p className="page-sub">
              {session ? (
                <>
                  Items waiting for {actorName(session.actor.id)}, {me?.label.toLowerCase()}, oldest first.
                </>
              ) : (
                'Loading'
              )}
            </p>
          </div>
        </header>
        <ErrorState error={error} what="your inbox" />
        {loading && !data ? (
          <Skeleton rows={6} cols={3} />
        ) : items.length === 0 ? (
          <div className="inbox-empty">
            <p className="empty-title">Nothing needs you right now.</p>
            <p className="ink-2">{session?.role === 'auditor' ? 'Auditors have read only access. Use Audit to ask why anything happened.' : 'Elix will put an item here when a check needs your decision.'}</p>
          </div>
        ) : (
          <ul className="inbox-list" role="listbox" aria-label="Inbox items">
            {items.map((it) => {
              const agent = world?.agents.find((a) => a.actor_id === it.requested_by);
              const vendor = world?.vendors.find((v) => v.id === it.vendor_id);
              return (
                <li key={it.id}>
                  <button type="button" role="option" aria-selected={sel === it.id} className={`inbox-row${sel === it.id ? ' is-selected' : ''}`} onClick={() => setSel(it.id)}>
                    <span className="inbox-row-top">
                      <Pill state={it.state} item={it} />
                      <span className="inbox-age ink-3 tnum">{age(it.created_at, now)}</span>
                    </span>
                    <span className="inbox-row-mid">
                      <span className="inbox-title truncate">{it.title}</span>
                      <Money cents={it.amount_cents || null} className="inbox-amt" />
                    </span>
                    <span className="inbox-row-bot">
                      <span className="ink-2 truncate">
                        <span className="mono ink-3">{it.id}</span> {agent ? `${agent.name}, ${vendor?.name ?? ''}` : vendor?.name ?? actorName(it.requested_by)}
                      </span>
                      <Flags flags={it.flags} max={it.flags.length > 2 ? 1 : 2} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="split-detail">{sel ? <ItemDetail key={sel} id={sel} /> : !loading && <div className="split-placeholder ink-3">Select an item to see its checks and decide.</div>}</div>
    </div>
  );
}
