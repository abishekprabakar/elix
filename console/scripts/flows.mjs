// Drives the presenter's human steps through the console UI against a running API.
// Usage: node scripts/flows.mjs --base http://127.0.0.1:8400 [--out dir]
// Expects the scenarios cross-rail-double-bill, payee-change, learn-policies and closed-period-fix to have run.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const req = createRequire('/home/claude/tools/shot/package.json');
const puppeteer = (await import(req.resolve('puppeteer-core'))).default;
const chromium = (await import(req.resolve('@sparticuz/chromium'))).default;
const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(`--${k}`) ? args[args.indexOf(`--${k}`) + 1] : d);
const base = opt('base', 'http://127.0.0.1:8400');
const out = opt('out', '');
if (out) mkdirSync(out, { recursive: true });

const api = async (path, token = 'tok_priya', init = {}) => {
  const r = await fetch(base + path, { ...init, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(init.headers ?? {}) } });
  return r.json();
};

const browser = await puppeteer.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true });
const results = [];
const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function open(path) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', (e) => errors.push(`${path}: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/status of 404/.test(m.text())) errors.push(`${path}: ${m.text()}`);
  });
  await page.goto(base + path, { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), { timeout: 15000 }).catch(() => {});
  await sleep(600);
  return page;
}

async function clickText(page, selector, text) {
  const handles = await page.$$(selector);
  for (const h of handles) {
    const t = await h.evaluate((el) => el.textContent?.trim() ?? '');
    const disabled = await h.evaluate((el) => el.disabled);
    if (t.startsWith(text) && !disabled) {
      await h.click();
      return true;
    }
  }
  return false;
}

async function bodyText(page) {
  return page.evaluate(() => document.body.innerText);
}

function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`);
}

async function shot(page, name) {
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

try {
  // 1. Sam approves the Lumen short pay on Reconcile
  {
    const page = await open('/reconcile?as=sam');
    const clicked = await clickText(page, '.invoice-panel .actions-row button', 'Approve short pay');
    await sleep(200);
    await clickText(page, '.invoice-panel .action-form button[type=submit]', 'Approve short pay');
    await sleep(1500);
    const t = await bodyText(page);
    await shot(page, 'flow_sam_short_pay');
    check('Sam approves the Lumen short pay', clicked && /Posted|Paid/.test(t) && /short pay approved|Short pay approved/i.test(t + ' '), clicked ? '' : 'button not found');
    await page.close();
  }

  // 2. Dev approves the Stormvault spend, the item stays on hold
  const storm = (await api('/api/work-items?vendor=ven_stormvault&limit=500')).items.find((i) => i.sku === 'archive-tx-2025' && i.state === 'NEEDS_APPROVAL');
  if (storm) {
    const page = await open(`/items/${storm.id}?as=dev`);
    const already = !!storm.data?.spend_approved;
    let clicked = already;
    if (!already) {
      clicked = await clickText(page, '.actions-row button', 'Approve spend');
      await sleep(200);
      await clickText(page, '.action-form button[type=submit]', 'Approve spend');
      await sleep(1500);
    }
    const t = await bodyText(page);
    await shot(page, 'flow_dev_spend');
    check('Dev approves the spend and the payment stays held', clicked && /Held for payee check/.test(t), already ? 'spend was already approved' : clicked ? '' : 'button not found');
    // Dev cannot verify the payee: disabled button with a reason
    check('Dev sees why he cannot verify the payee', /Verify payee/.test(t) && /controller/i.test(t));
    await page.close();

    // 3. Priya verifies the payee and the gateway pays
    const p2 = await open(`/items/${storm.id}?as=priya`);
    const c2 = await clickText(p2, '.actions-row button', 'Verify payee');
    await sleep(200);
    await p2.type('.action-form textarea', 'Called Stormvault at the number on the 2025 contract');
    await clickText(p2, '.action-form button[type=submit]', 'Verify payee');
    await sleep(2000);
    const t2 = await bodyText(p2);
    await shot(p2, 'flow_priya_verify');
    check('Priya verifies the payee and the gateway pays', c2 && /Posted|Paid/.test(t2), c2 ? '' : 'button not found');
    await p2.close();
  } else check('Stormvault archive item exists', false, 'run the payee-change scenario first');

  // 4. A budget owner tries to approve a learned policy and the API refuses
  {
    const page = await open('/policies?as=dev');
    const clicked = await clickText(page, '.policy-actions button', 'Approve policy');
    await sleep(1200);
    const t = await bodyText(page);
    check('Dev is refused when approving a policy, with the API message', clicked && /controller/i.test(t) && /Refused by the API|controller/.test(t), clicked ? '' : 'no proposed policy');
    await shot(page, 'flow_dev_policy_refused');
    await page.close();
  }

  // 5. Priya approves the learned policies
  {
    const page = await open('/policies?as=priya');
    let n = 0;
    for (let i = 0; i < 6; i++) {
      const ok = await clickText(page, '.policy-actions button', 'Approve policy');
      if (!ok) break;
      n++;
      await sleep(1200);
    }
    const t = await bodyText(page);
    await shot(page, 'flow_priya_policies');
    check('Priya approves the learned policies', n > 0 && /Active/.test(t), `${n} approved`);
    await page.close();
  }

  // 6. Priya approves the closed period correction
  {
    const inbox = await api('/api/work-items?inbox=me&limit=100', 'tok_priya');
    const corr = inbox.items.find((i) => i.kind === 'correction');
    if (corr) {
      const page = await open(`/items/${corr.id}?as=priya`);
      const clicked = await clickText(page, '.actions-row button', 'Approve correction');
      await sleep(200);
      await clickText(page, '.action-form button[type=submit]', 'Approve correction');
      await sleep(1500);
      const t = await bodyText(page);
      await shot(page, 'flow_priya_correction');
      check('Priya approves the correction', clicked && /Posted|Approved/.test(t), clicked ? '' : 'button not found');
      await page.close();
    } else check('Correction waiting for Priya', false, 'run closed-period-fix first');
  }

  // 7. Jordan asks why LUM-2026-10A was paid short
  {
    const page = await open('/audit?as=jordan');
    await clickText(page, '.why-presets button', 'Why was LUM-2026-10A');
    await sleep(1500);
    const t = await bodyText(page);
    await shot(page, 'flow_jordan_why');
    check('Jordan gets the why answer for LUM-2026-10A', /1,260/.test(t) && /LUM-2026-10A/.test(t));
    await page.close();
  }
} finally {
  await browser.close();
}
console.log(errors.length ? 'CONSOLE ERRORS\n  ' + errors.join('\n  ') : 'no console errors');
const failed = results.filter((r) => !r.ok).length;
console.log(`${results.length - failed} of ${results.length} flows passed`);
process.exit(failed ? 1 : 0);
