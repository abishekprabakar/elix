import { useMemo, useState } from 'react';
import type { Summary } from '../api/types';
import { calDate, money, moneyAxis } from '../lib/format';
import { useWidth } from '../lib/hooks';

function niceStep(max: number, ticks = 4) {
  if (max <= 0) return 100;
  const raw = max / ticks;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * pow >= raw) return m * pow;
  return 10 * pow;
}

export function SpendChart({ daily, period, height = 196, mode }: { daily: Summary['daily_spend']; period: string; height?: number; mode: 'all' | 'x402' }) {
  const [ref, width] = useWidth<HTMLDivElement>(720);
  const [hover, setHover] = useState<number | null>(null);
  const [y, m] = period.split('-').map(Number);
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const byDay = useMemo(() => {
    const map = new Map(daily.map((d) => [Number(d.date.slice(8, 10)), d]));
    return Array.from({ length: daysInMonth }, (_, i) => map.get(i + 1) ?? null);
  }, [daily, daysInMonth]);

  const val = (d: Summary['daily_spend'][number] | null) => (d ? d.x402_cents + (mode === 'all' ? d.bank_cents : 0) : 0);
  const max = Math.max(1, ...byDay.map(val));
  const step = niceStep(max);
  const top = Math.ceil(max / step) * step;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);

  const W = Math.max(320, width);
  const padL = 46;
  const padR = 4;
  const padT = 10;
  const padB = 24;
  const plotW = W - padL - padR;
  const plotH = height - padT - padB;
  const band = plotW / daysInMonth;
  const barW = Math.max(3, Math.min(18, band * 0.62));
  const yOf = (c: number) => padT + plotH - (c / top) * plotH;
  const labelDays = [1, 5, 10, 15, 20, 25, daysInMonth];
  const h = hover != null ? byDay[hover] : null;

  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`Daily spend for ${period}`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={yOf(t)} y2={yOf(t)} className={t === 0 ? 'chart-base' : 'chart-grid'} />
            <text x={padL - 8} y={yOf(t)} className="chart-label" textAnchor="end" dominantBaseline="middle">
              {moneyAxis(t)}
            </text>
          </g>
        ))}
        {byDay.map((d, i) => {
          const cx = padL + band * i + band / 2;
          const x = cx - barW / 2;
          const xv = d?.x402_cents ?? 0;
          const bv = mode === 'all' ? d?.bank_cents ?? 0 : 0;
          const xh = (xv / top) * plotH;
          const bh = (bv / top) * plotH;
          const isFuture = d == null;
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={padL + band * i} y={padT} width={band} height={plotH} fill="transparent" />
              {hover === i && <rect x={padL + band * i + 1} y={padT} width={band - 2} height={plotH} className="chart-hover" />}
              {isFuture ? (
                <rect x={x} y={yOf(0) - 2} width={barW} height={2} className="chart-future" />
              ) : (
                <>
                  {xv > 0 && <rect x={x} y={yOf(0) - xh} width={barW} height={Math.max(1, xh)} className="chart-x402" />}
                  {bv > 0 && <rect x={x} y={yOf(0) - xh - bh - (xv > 0 ? 1 : 0)} width={barW} height={Math.max(1, bh)} className="chart-bank" />}
                </>
              )}
              {labelDays.includes(i + 1) && (
                <text x={cx} y={height - 6} className="chart-label" textAnchor="middle">
                  {i === 0 ? calDate(`${period}-01`) : i + 1}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {h && hover != null && (
        <div className="chart-tip" style={{ left: Math.min(W - 180, Math.max(0, padL + band * hover + band / 2 - 90)) }}>
          <p className="chart-tip-date">{calDate(h.date)}</p>
          <p className="tnum">
            <span className="key key-x402" /> x402 <span className="chart-tip-v">{money(h.x402_cents)}</span>
          </p>
          {mode === 'all' && (
            <p className="tnum">
              <span className="key key-bank" /> Bank <span className="chart-tip-v">{money(h.bank_cents)}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
