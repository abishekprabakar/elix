// Formatting helpers. Amounts are integer cents.

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 });
const num = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });

export const TZ = 'UTC';

export function money(cents: number | null | undefined): string {
  if (cents == null) return '';
  return usd.format(cents / 100);
}

export function moneyWhole(cents: number | null | undefined): string {
  if (cents == null) return '';
  return usd0.format(Math.round(cents / 100));
}

/** Compact money for axis labels: $0, $5k, $12.5k */
export function moneyAxis(cents: number): string {
  const d = cents / 100;
  if (d === 0) return '$0';
  if (d >= 1000) {
    const k = d / 1000;
    return '$' + (Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1)) + 'k';
  }
  return '$' + d.toFixed(0);
}

export function usdc(cents: number): string {
  return (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' USDC';
}

export function qty(n: number | null | undefined): string {
  if (n == null) return '';
  return num.format(n);
}

export function pct(x: number, digits = 0): string {
  return (x * 100).toFixed(digits) + '%';
}

const dtf = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...o });
const fDate = dtf({ month: 'short', day: 'numeric' });
const fDateY = dtf({ month: 'short', day: 'numeric', year: 'numeric' });
const fTime = dtf({ hour: 'numeric', minute: '2-digit' });
const fTimeS = dtf({ hour: 'numeric', minute: '2-digit', second: '2-digit' });
const fLong = dtf({ weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });

export function date(s: string | null | undefined): string {
  if (!s) return '';
  return fDate.format(parseDate(s));
}
export function dateYear(s: string | null | undefined): string {
  if (!s) return '';
  return fDateY.format(parseDate(s));
}
export function time(s: string | null | undefined): string {
  if (!s) return '';
  return fTime.format(parseDate(s));
}
export function timeSec(s: string | null | undefined): string {
  if (!s) return '';
  return fTimeS.format(parseDate(s));
}
export function dateTime(s: string | null | undefined): string {
  if (!s) return '';
  return `${date(s)}, ${time(s)}`;
}
export function longDate(s: string): string {
  return fLong.format(parseDate(s));
}

/** Calendar dates like 2026-10-09 are formatted in UTC so they never shift a day. */
export function calDate(s: string): string {
  if (!s) return '';
  const d = new Date(s.length === 10 ? s + 'T12:00:00Z' : s);
  if (isNaN(d.getTime())) return s;
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' }).format(d);
}

function parseDate(s: string): Date {
  const d = new Date(s.length === 10 ? s + 'T12:00:00Z' : s);
  return isNaN(d.getTime()) ? new Date(0) : d;
}

export function periodLabel(p: string): string {
  if (!p || !/^\d{4}-\d{2}$/.test(p)) return '';
  const d = new Date(p + '-15T12:00:00Z');
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'long', year: 'numeric' }).format(d);
}
export function periodMonth(p: string): string {
  if (!p || !/^\d{4}-\d{2}$/.test(p)) return '';
  const d = new Date(p + '-15T12:00:00Z');
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'long' }).format(d);
}

/** Waiting time relative to the world clock, e.g. "18 min", "3 h", "2 d". */
export function age(from: string, now: string): string {
  const ms = new Date(now).getTime() - new Date(from).getTime();
  if (ms < 0) return 'just now';
  const m = Math.floor(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.floor(h / 24)} d`;
}

export function ago(from: string, now: string): string {
  const a = age(from, now);
  return a === 'just now' ? a : `${a} ago`;
}

export function shortHash(h: string | null | undefined, n = 10): string {
  if (!h) return '';
  return h.slice(0, n);
}

export function shortAddr(a: string | null | undefined): string {
  if (!a) return '';
  if (a.length <= 14) return a;
  return a.slice(0, 6) + '…' + a.slice(-4);
}

export function plural(n: number, one: string, many?: string): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many ?? one + 's'}`;
}

export function sentenceCase(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Lower the first letter, unless it starts a proper noun or acronym. */
export function lowerFirst(s: string): string {
  if (!s) return s;
  if (/^[A-Z]{2}|^[A-Z][a-z]+ [A-Z]/.test(s)) return s;
  return s.charAt(0).toLowerCase() + s.slice(1);
}
