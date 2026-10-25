import type { ApiErrorBody } from './types';

export const FIXTURES = import.meta.env.VITE_FIXTURES === '1';

export class ApiError extends Error {
  status: number;
  code: string;
  ruleId: string | null;
  auditSeq: number | null;
  constructor(status: number, code: string, message: string, ruleId: string | null = null, auditSeq: number | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.ruleId = ruleId;
    this.auditSeq = auditSeq;
  }
}

let currentToken = 'tok_priya';
export function setToken(t: string) {
  currentToken = t;
}
export function getToken() {
  return currentToken;
}

type MockHandler = (method: string, path: string, body: unknown, token: string) => Promise<{ status: number; body: unknown }>;
let mock: MockHandler | null = null;
async function getMock(): Promise<MockHandler> {
  if (!mock) {
    const m = await import('./mock');
    mock = m.handle;
  }
  return mock;
}

export const API_BASE = '';

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? 'GET';
  if (FIXTURES) {
    const h = await getMock();
    const r = await h(method, path, init.body, currentToken);
    if (r.status >= 400) throw toError(r.status, r.body);
    return r.body as T;
  }
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: {
        Authorization: `Bearer ${currentToken}`,
        ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'offline', 'The API at 127.0.0.1:8400 is not responding. Start it with python -m elix demo.');
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: { code: 'invalid', message: text.slice(0, 200) } };
    }
  }
  if (!res.ok) {
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new ApiError(res.status, 'offline', 'The API at 127.0.0.1:8400 is not responding. Start it with python -m elix demo.');
    }
    throw toError(res.status, body);
  }
  return body as T;
}

/** POST a multipart form, for file uploads. The browser sets the multipart boundary itself. */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  if (FIXTURES) throw new ApiError(400, 'invalid', 'Uploads need the real API. Fixture mode has no PDF intake.');
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { method: 'POST', headers: { Authorization: `Bearer ${currentToken}` }, body: form });
  } catch {
    throw new ApiError(0, 'offline', 'The API at 127.0.0.1:8400 is not responding. Start it with python -m elix demo.');
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: { code: 'invalid', message: text.slice(0, 200) } };
  }
  if (!res.ok) throw toError(res.status, body);
  return body as T;
}

/** GET a file with the bearer token, for the sample invoice PDFs. */
export async function apiBlob(path: string): Promise<Blob> {
  if (FIXTURES) throw new ApiError(400, 'invalid', 'Fixture mode has no sample files.');
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { headers: { Authorization: `Bearer ${currentToken}` } });
  } catch {
    throw new ApiError(0, 'offline', 'The API at 127.0.0.1:8400 is not responding. Start it with python -m elix demo.');
  }
  if (!res.ok) {
    const text = await res.text();
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = null;
    }
    throw toError(res.status, body);
  }
  return res.blob();
}

function toError(status: number, body: unknown): ApiError {
  const b = body as Partial<ApiErrorBody> & { detail?: unknown };
  if (b && b.error) {
    return new ApiError(status, b.error.code ?? 'error', b.error.message ?? 'The request failed.', b.error.rule_id ?? null, b.error.audit_seq ?? null);
  }
  if (b && typeof b.detail === 'string') return new ApiError(status, 'error', b.detail);
  if (b && b.detail && typeof b.detail === 'object' && 'error' in (b.detail as object)) {
    const e = (b.detail as ApiErrorBody).error;
    return new ApiError(status, e.code, e.message, e.rule_id ?? null, e.audit_seq ?? null);
  }
  if (status === 404) return new ApiError(status, 'not_found', 'Not found.');
  return new ApiError(status, 'error', `The request failed with status ${status}.`);
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? '?' + s : '';
}
