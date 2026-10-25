import { useState } from 'react';
import type { PaymentChallenge, Receipt, Vendor, X402Panel } from '../api/types';
import { dateTime, shortAddr, timeSec, usdc } from '../lib/format';
import { IconCheck, IconLock } from './Icons';

/** Fine engraved band drawn once as an SVG pattern, like the top edge of check safety paper. */
export function Guilloche({ height = 18, id = 'g' }: { height?: number; id?: string }) {
  const pid = `guilloche-${id}`;
  const h = height;
  const m = h / 2;
  const wave = (amp: number, phase: number) => {
    const w = 40;
    const x0 = -phase;
    return `M${x0} ${m} C ${x0 + w * 0.25} ${m - amp}, ${x0 + w * 0.25} ${m - amp}, ${x0 + w / 2} ${m} S ${x0 + w * 0.75} ${m + amp}, ${x0 + w} ${m} S ${x0 + w * 1.25} ${m - amp}, ${x0 + w * 1.5} ${m} S ${x0 + w * 1.75} ${m + amp}, ${x0 + w * 2} ${m}`;
  };
  return (
    <svg className="guilloche" width="100%" height={h} aria-hidden="true">
      <defs>
        <pattern id={pid} width="40" height={h} patternUnits="userSpaceOnUse">
          {[0, 10, 20, 30].map((ph) => (
            <path key={'a' + ph} d={wave(m - 1.5, ph)} fill="none" stroke="var(--engrave)" strokeWidth="0.9" />
          ))}
          {[5, 25].map((ph) => (
            <path key={'b' + ph} d={wave(m * 0.45, ph)} fill="none" stroke="var(--engrave)" strokeWidth="0.9" />
          ))}
        </pattern>
      </defs>
      <rect width="100%" height={h} fill={`url(#${pid})`} />
    </svg>
  );
}

function Addr({ a }: { a: string }) {
  return (
    <span className="mono addr" title={a}>
      {a}
    </span>
  );
}

export function ReceiptBlock({
  challenge,
  receipt,
  vendor,
  panel,
  payeeHold,
  title,
  failed,
}: {
  challenge: PaymentChallenge | null | undefined;
  receipt: Receipt | null | undefined;
  vendor?: Vendor;
  panel?: X402Panel | null;
  payeeHold?: boolean;
  title?: string;
  failed?: string | null;
}) {
  const [raw, setRaw] = useState(false);
  const simulated = receipt ? receipt.simulated : true;
  const bank = receipt?.rail === 'bank';
  const changed = vendor?.payout_address_changed_at;
  const prev = vendor?.previous_payout_address;
  const decoded = panel?.challenge_decoded ?? safeDecode(challenge?.raw_b64);

  return (
    <div className={`receipt${payeeHold ? ' receipt-hold' : ''}`}>
      <Guilloche id={(challenge?.resource ?? receipt?.tx_ref ?? 'r').slice(-12).replace(/\W/g, '')} />
      <div className="receipt-body">
        <div className="receipt-head">
          <div>
            <p className="receipt-kind">{bank ? 'Bank payment' : 'x402 payment'}</p>
            {title && <p className="receipt-title">{title}</p>}
          </div>
          {simulated && <span className="badge-sim">Simulated settlement</span>}
        </div>

        {challenge && (
          <>
            <div className="protocol mono">
              <span className="protocol-status">HTTP 402</span>
              <span>Payment Required</span>
              <span className="protocol-time ink-3">{timeSec(challenge.received_at)}</span>
            </div>
            <dl className="receipt-grid">
              <div>
                <dt>Amount</dt>
                <dd>
                  <span className="receipt-amount tnum">{usdc(challenge.amount_cents)}</span>
                  <span className="mono ink-3 receipt-atomic">{challenge.amount_atomic} atomic units</span>
                </dd>
              </div>
              <div>
                <dt>Network</dt>
                <dd>
                  <span>Base Sepolia</span>
                  <span className="mono ink-3 receipt-atomic">{challenge.network}</span>
                </dd>
              </div>
              <div className={`receipt-wide${payeeHold ? ' receipt-flag' : ''}`}>
                <dt>Pay to</dt>
                <dd>
                  <Addr a={challenge.pay_to} />
                  {payeeHold && prev && changed && (
                    <span className="receipt-note">
                      <IconLock size={12} />
                      <span>
                        Vendor record changed from <span className="mono">{shortAddr(prev)}</span> on {dateTime(changed)}
                      </span>
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Asset</dt>
                <dd>
                  <span>
                    USDC <span className="mono ink-3">{shortAddr(challenge.asset)}</span>
                  </span>
                </dd>
              </div>
              <div>
                <dt>Scheme</dt>
                <dd>
                  <span>
                    <span className="mono">{challenge.scheme}</span>, valid {challenge.max_timeout_seconds} seconds
                  </span>
                </dd>
              </div>
              <div className="receipt-wide">
                <dt>Resource</dt>
                <dd>
                  <span className="mono receipt-url" title={challenge.resource}>
                    {challenge.resource}
                  </span>
                </dd>
              </div>
            </dl>
          </>
        )}

        <div className="perforation" aria-hidden="true" />

        {receipt ? (
          <dl className="receipt-grid">
            {receipt.payload_sha256 && (
              <div className="receipt-wide">
                <dt>Signed payload</dt>
                <dd>
                  <span className="mono hash">sha256 {receipt.payload_sha256}</span>
                </dd>
              </div>
            )}
            {receipt.signature_verified != null && (
              <div>
                <dt>Signature</dt>
                <dd className={receipt.signature_verified ? 'ok' : 'deny'}>
                  <span className="verify">
                    <IconCheck size={13} /> {receipt.signature_verified ? 'EIP-3009 signature verified' : 'Signature not verified'}
                  </span>
                </dd>
              </div>
            )}
            <div>
              <dt>Settlement</dt>
              <dd>
                <span className="mono">{receipt.tx_ref}</span>
              </dd>
            </div>
            <div>
              <dt>Settled</dt>
              <dd className="tnum">{dateTime(receipt.settled_at)}</dd>
            </div>
            {!bank && (
              <div>
                <dt>Resource delivered</dt>
                <dd>{receipt.resource_delivered ? <span className="mono">sha256 {receipt.resource_sha256?.slice(0, 16)}</span> : 'No'}</dd>
              </div>
            )}
            <div className={bank ? '' : 'receipt-wide'}>
              <dt>{bank ? 'From' : 'Payer, Kestrel treasury'}</dt>
              <dd>{bank ? receipt.payer : <Addr a={receipt.payer} />}</dd>
            </div>
            {bank && (
              <div>
                <dt>To</dt>
                <dd>{receipt.payee}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className={`receipt-unpaid ${failed != null ? 'deny' : 'ink-2'}`}>
            {failed != null
              ? `Not paid. The rail refused the payment, so no money moved.${failed ? ` ${failed}` : ''}`
              : payeeHold
              ? 'Not paid. The gateway signs the payment only after the controller verifies the new payee, and refuses if the vendor changes the terms again.'
              : 'Not paid yet. The gateway signs and pays only after every required approval is in.'}
          </p>
        )}

        {decoded && (
          <div className="receipt-raw">
            <button type="button" className="btn btn-ghost btn-sm" aria-expanded={raw} onClick={() => setRaw((r) => !r)}>
              {raw ? 'Hide raw' : 'View raw'}
            </button>
            {raw && (
              <pre className="code">
                {JSON.stringify(
                  {
                    'PAYMENT-REQUIRED': decoded,
                    ...(panel?.payload_decoded ? { 'PAYMENT-SIGNATURE': panel.payload_decoded } : {}),
                    ...(panel?.payment_response_decoded ? { 'PAYMENT-RESPONSE': panel.payment_response_decoded } : {}),
                    ...(panel?.payload_sha256 ? { payload_sha256: panel.payload_sha256 } : {}),
                  },
                  null,
                  2,
                )}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function safeDecode(b64?: string | null): Record<string, unknown> | null {
  if (!b64) return null;
  try {
    return JSON.parse(atob(b64));
  } catch {
    return null;
  }
}
