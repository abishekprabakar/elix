import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { AgentSession, AgentSessionDetail, AgentStatus, TranscriptEntry, WorkItemList } from '../api/types';
import { Avatar, ErrorState, InlineError, ItemLink, Money, Skeleton } from '../components/Bits';
import { ElixirMeter } from '../components/Meter';
import { Pill } from '../components/Pill';
import { ago, dateTime, moneyWhole, timeSec } from '../lib/format';
import { useApi, useStore } from '../lib/store';
import { useDrawer } from '../shell/Drawer';

const DEFAULT_TASKS: Record<string, string> = {
  'agent:atlas': 'Assemble 10 m imagery for the Sept 28 hailstorm footprint in Buffalo County, Nebraska, for the hail-v7 training set.',
  'agent:forge': 'Reserve 400 H100 spot hours to retrain hail-v7 on the new Nebraska scenes this weekend.',
  'agent:scout': 'Enrich 300 crop insurance carriers in Kansas with firmographics for the November outreach list.',
};

export function Agents() {
  const { data, error, loading } = useApi<{ agents: AgentStatus[] }>('/api/agents');
  const [focus, setFocus] = useState<string>(() => {
    const a = new URLSearchParams(window.location.search).get('agent');
    return a ? `agent:${a}` : 'agent:atlas';
  });
  const agents = data?.agents ?? [];
  const cur = agents.find((a) => a.actor_id === focus) ?? agents[0];
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Agents</h1>
          <p className="page-sub">Each agent can ask to buy, answer questions about its own requests, and nothing else. None of them holds a key or can approve.</p>
        </div>
      </header>
      <ErrorState error={error} what="agents" />
      {loading && !data ? (
        <Skeleton rows={4} cols={4} />
      ) : (
        <>
          <div className="agent-tabs" role="tablist">
            {agents.map((a) => (
              <button key={a.actor_id} type="button" role="tab" aria-selected={cur?.actor_id === a.actor_id} className={`agent-tab${cur?.actor_id === a.actor_id ? ' is-on' : ''}`} onClick={() => setFocus(a.actor_id)}>
                <span className="agent-tab-head">
                  <Avatar id={a.actor_id} size={20} />
                  <span className="agent-tab-name">{a.name}</span>
                  <span className={`mode mode-${a.mode}`}>{a.mode === 'claude' ? 'Claude' : 'Scripted agent (offline)'}</span>
                </span>
                <ElixirMeter spent={a.spent_cents} committed={a.committed_cents} budget={a.monthly_budget_cents} cap={a.per_txn_cap_cents} />
              </button>
            ))}
          </div>
          {cur && <AgentPanel a={cur} />}
        </>
      )}
    </div>
  );
}

function AgentPanel({ a }: { a: AgentStatus }) {
  const { actorName, session, now, toast } = useStore();
  const { open } = useDrawer();
  const short = a.actor_id.replace('agent:', '');
  const { data: reqs } = useApi<WorkItemList>(`/api/work-items?agent=${a.actor_id}&kind=purchase&limit=8`);
  const { data: sess } = useApi<{ sessions: AgentSession[] }>(`/api/agents/sessions?agent=${a.actor_id}`);
  const [task, setTask] = useState(DEFAULT_TASKS[a.actor_id] ?? '');
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    setTask(DEFAULT_TASKS[a.actor_id] ?? '');
    setSel(null);
    setErr(null);
  }, [a.actor_id]);
  const sessions = useMemo(() => [...(sess?.sessions ?? [])].sort((x, y) => (x.started_at < y.started_at ? 1 : -1)), [sess]);
  const shown = sel ?? sessions[0]?.id ?? null;

  const run = async () => {
    setRunning(true);
    setErr(null);
    try {
      const r = await api<{ session_id: string }>(`/api/agents/${short}/run`, { method: 'POST', body: { task } });
      setSel(r.session_id);
      toast(`${a.name} started. The transcript fills in as it works.`, 'ok');
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="agent-panel">
      <div className="agent-left">
        <dl className="kv agent-facts">
          <div className="kv-row">
            <dt>Owner</dt>
            <dd>
              <Avatar id={a.owner_id} size={16} /> {actorName(a.owner_id)}
            </dd>
          </div>
          <div className="kv-row">
            <dt>Cost center</dt>
            <dd>{a.cost_center}</dd>
          </div>
          <div className="kv-row">
            <dt>Monthly budget</dt>
            <dd className="tnum">{moneyWhole(a.monthly_budget_cents)}</dd>
          </div>
          <div className="kv-row">
            <dt>Per purchase cap</dt>
            <dd className="tnum">{moneyWhole(a.per_txn_cap_cents)}</dd>
          </div>
          <div className="kv-row">
            <dt>Auto approve under</dt>
            <dd className="tnum">{moneyWhole(a.auto_approve_under_cents)} by default</dd>
          </div>
          <div className="kv-row">
            <dt>Last request</dt>
            <dd className="tnum">{a.last_activity_at ? ago(a.last_activity_at, now) : 'None'}</dd>
          </div>
        </dl>
        <p className="ink-2 agent-desc">{a.description}</p>

        <section className="section">
          <div className="section-head">
            <h2 className="section-title">Recent requests</h2>
            <Link className="section-link" to={`/activity?agent=${a.actor_id}`}>
              See all
            </Link>
          </div>
          {!reqs ? (
            <Skeleton rows={5} cols={2} />
          ) : (
            <ul className="req-list">
              {reqs.items.slice(0, 7).map((it) => (
                <li key={it.id}>
                  <button type="button" className="req-row" onClick={() => open(it.id)}>
                    <span className="req-top">
                      <span className="truncate req-title">{it.title}</span>
                      <Money cents={it.amount_cents} className="req-amt" />
                    </span>
                    <span className="req-bot">
                      <Pill state={it.state} item={it} />
                      <span className="mono ink-3">{it.id}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="agent-right">
        <div className="agent-run panel">
          <div className="agent-run-head">
            <h2 className="section-title">Run {a.name}</h2>
            <span className="ink-3 agent-run-note">{a.mode === 'claude' ? 'Runs with Claude through the same API an outside agent would use.' : 'No Anthropic key is set, so the scripted agent runs offline through the same API.'}</span>
          </div>
          <label className="field">
            <span className="field-label">Task</span>
            <textarea className="input" rows={2} value={task} onChange={(e) => setTask(e.target.value)} />
          </label>
          <div className="agent-run-row">
            <button type="button" className="btn btn-primary" disabled={running || !task.trim() || session?.actor.kind !== 'human'} onClick={run}>
              {running ? 'Starting' : `Run ${a.name}`}
            </button>
          </div>
          <InlineError message={err} />
        </div>

        <section className="section">
          <div className="section-head">
            <h2 className="section-title">Sessions</h2>
            {sessions.length > 0 && (
              <div className="session-tabs">
                {sessions.slice(0, 5).map((s) => (
                  <button key={s.id} type="button" className={`session-tab${shown === s.id ? ' is-on' : ''}`} onClick={() => setSel(s.id)}>
                    <span className="mono">{s.id}</span>
                    <span className="ink-3 tnum">{dateTime(s.started_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          {sessions.length === 0 ? <p className="ink-3">No runs yet. Run {a.name} to see its transcript here.</p> : shown && <Transcript id={shown} />}
        </section>
      </div>
    </div>
  );
}

function Transcript({ id }: { id: string }) {
  const { data, reload } = useApi<AgentSessionDetail>(`/api/agents/sessions/${id}`);
  const runningNow = data && !data.ended_at;
  useEffect(() => {
    if (!runningNow) return;
    const t = window.setInterval(reload, 1500);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningNow, id]);
  if (!data) return <Skeleton rows={4} cols={2} />;
  return (
    <div className="transcript">
      <div className="transcript-head">
        <span className={`mode mode-${data.mode}`}>{data.mode === 'claude' ? `Claude agent${data.model ? `, ${data.model}` : ''}` : 'Scripted agent (offline)'}</span>
        <span className="ink-2">{statusText(data.status)}</span>
        {data.request_ids.length > 0 && (
          <span className="transcript-ids">
            Created{' '}
            {data.request_ids.map((r) => (
              <ItemLink key={r} id={r} />
            ))}
          </span>
        )}
      </div>
      {(data.summary || data.error) && (
        <div className="transcript-summary">
          {data.summary && <p>{data.summary}</p>}
          {data.error && <p className="deny">{data.error}</p>}
        </div>
      )}
      {data.transcript.length === 0 ? (
        <p className="ink-3">{runningNow ? 'Waiting for the first step.' : 'This session recorded no transcript.'}</p>
      ) : (
        <ol className="turns">
          {data.transcript.map((t, i) => (
            <Turn key={i} t={t} />
          ))}
        </ol>
      )}
    </div>
  );
}

function statusText(s: string) {
  const m: Record<string, string> = {
    waiting_on_human: 'Stopped and waiting on a person',
    running: 'Running',
    done: 'Finished',
    completed: 'Finished',
    max_turns: 'Stopped at the turn limit',
    error: 'Stopped with an error',
    failed: 'Stopped with an error',
  };
  return m[s] ?? s.replace(/_/g, ' ');
}

function Turn({ t }: { t: TranscriptEntry }) {
  if (t.role === 'tool_call')
    return (
      <li className="turn turn-call">
        <span className="turn-time tnum ink-3">{timeSec(t.at)}</span>
        <div>
          <p className="turn-label">Tool call</p>
          <pre className="code code-sm">
            {t.tool}({t.args && Object.keys(t.args).length ? JSON.stringify(t.args, null, 2) : ''})
          </pre>
        </div>
      </li>
    );
  if (t.role === 'tool_result')
    return (
      <li className="turn turn-result">
        <span className="turn-time tnum ink-3">{timeSec(t.at)}</span>
        <div>
          <p className="turn-label">Result from {t.tool}</p>
          <pre className="code code-sm">{summarize(t.result ?? t.text)}</pre>
        </div>
      </li>
    );
  return (
    <li className={`turn turn-${t.role}`}>
      <span className="turn-time tnum ink-3">{timeSec(t.at)}</span>
      <div>
        <p className="turn-label">{t.role === 'user' ? 'Task' : 'Agent'}</p>
        <p className="turn-text">{t.text}</p>
      </div>
    </li>
  );
}

function summarize(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v.length > 600 ? v.slice(0, 600) + '…' : v;
  const s = JSON.stringify(v, null, 2);
  return s.length > 900 ? s.slice(0, 900) + '\n…' : s;
}
