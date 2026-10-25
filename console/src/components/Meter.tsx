import { moneyWhole } from '../lib/format';

/**
 * Budget meter. One thin bar for the month: solid ink is spent, hatched is committed (waiting for
 * approval), the track is what is left. Past the budget the bar turns deny. A small tick marks the
 * per purchase cap on the same scale.
 */
export function ElixirMeter({
  spent,
  committed,
  budget,
  cap,
  showNumbers = true,
}: {
  spent: number;
  committed: number;
  budget: number;
  cap?: number;
  pips?: number;
  height?: number;
  showNumbers?: boolean;
}) {
  const total = spent + committed;
  const over = budget > 0 && total > budget;
  const scale = budget > 0 ? Math.max(budget, total) : 1;
  const sP = Math.max(0, Math.min(100, (spent / scale) * 100));
  const cP = Math.max(0, Math.min(100 - sP, (committed / scale) * 100));
  const capP = cap && budget ? Math.min(100, (cap / scale) * 100) : null;
  const pct = budget ? Math.round((total / budget) * 100) : 0;
  const label = `${moneyWhole(spent)} spent and ${moneyWhole(committed)} waiting of a ${moneyWhole(budget)} budget, ${pct} percent used`;

  return (
    <div className="meter">
      <div className={`bar${over ? ' bar-over' : ''}`} role="img" aria-label={label}>
        <span className="bar-spent" style={{ width: `${sP}%` }} />
        <span className="bar-committed" style={{ width: `${cP}%` }} />
        {over && <span className="bar-budget" style={{ left: `${(budget / scale) * 100}%` }} />}
        {capP != null && <span className="bar-cap" style={{ left: `${capP}%` }} title={`Per purchase cap ${moneyWhole(cap ?? 0)}`} />}
      </div>
      {showNumbers && (
        <div className="meter-nums tnum">
          <span>
            <span className="meter-spent">{moneyWhole(spent)}</span>
            <span className="ink-2"> of {moneyWhole(budget)}</span>
          </span>
          {committed > 0 && (
            <span className="meter-committed">
              <span className="swatch-committed" aria-hidden="true" />
              {moneyWhole(committed)} waiting
            </span>
          )}
        </div>
      )}
    </div>
  );
}
