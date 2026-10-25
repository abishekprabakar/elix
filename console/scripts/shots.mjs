// Screenshots of every console screen at 1440x900 in light and dark.
// Usage: node scripts/shots.mjs --base http://127.0.0.1:8400 [--out screenshots] [--only today,inbox] [--fixtures]
// Uses puppeteer-core and @sparticuz/chromium from /home/claude/tools/shot.
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const req = createRequire('/home/claude/tools/shot/package.json');
const puppeteer = (await import(req.resolve('puppeteer-core'))).default;
const chromium = (await import(req.resolve('@sparticuz/chromium'))).default;

const args = process.argv.slice(2);
const opt = (k, d) => {
  const i = args.indexOf(`--${k}`);
  return i >= 0 ? args[i + 1] : d;
};
const flag = (k) => args.includes(`--${k}`);
const base = opt('base', 'http://127.0.0.1:4173');
const out = opt('out', join(here, '..', 'screenshots'));
const only = opt('only', '')?.split(',').filter(Boolean);
const themes = (opt('themes', 'light,dark') ?? 'light,dark').split(',');
const width = Number(opt('width', 1440));
const height = Number(opt('height', 900));
const fixtures = flag('fixtures');
mkdirSync(out, { recursive: true });

async function ids() {
  if (fixtures) {
    const items = JSON.parse(readFileSync(join(here, '..', 'src', 'fixtures', 'work-items.json'), 'utf8'));
    const storm = items.find((i) => i.sku === 'archive-tx-2025');
    const forge = items.find((i) => i.sku === 'h100-spot-hr' && i.quantity === 600 && i.created_at.startsWith('2026-10-09'));
    return { storm: storm?.id, forge: forge?.id };
  }
  const get = async (p) => (await fetch(base + p, { headers: { Authorization: 'Bearer tok_priya' } })).json();
  const s = await get('/api/work-items?vendor=ven_stormvault&limit=500');
  const storm = s.items.find((i) => i.sku === 'archive-tx-2025' && i.state === 'NEEDS_APPROVAL') ?? s.items.find((i) => i.sku === 'archive-tx-2025');
  const l = await get('/api/work-items?vendor=ven_lumen&agent=agent:forge&limit=500');
  const forge = l.items.find((i) => i.quantity === 600 && i.created_at.startsWith('2026-10-09') && i.receipt) ?? l.items.find((i) => i.receipt && i.challenge);
  return { storm: storm?.id, forge: forge?.id };
}

const { storm, forge } = await ids();
console.log('ids', { storm, forge });

const shots = [
  { name: 'today', path: '/?as=priya' },
  { name: 'inbox', path: '/inbox?as=priya' },
  { name: 'item_payee_hold', path: `/items/${storm}?as=priya` },
  { name: 'item_x402_receipt', path: `/items/${forge}?as=dev`, scrollBottom: '.receipt', offset: 20 },
  { name: 'reconcile', path: '/reconcile?as=sam' },
  { name: 'ledger', path: '/ledger?as=sam' },
  { name: 'policies', path: '/policies?as=priya' },
  { name: 'agents', path: '/agents?as=dev' },
  { name: 'audit_why', path: '/audit?as=jordan', click: '.why-presets button' },
  { name: 'proof', path: '/proof?as=priya' },
  { name: 'scenarios', path: '/scenarios?as=priya' },
];

const browser = await puppeteer.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true });
const errors = [];
try {
  for (const theme of themes) {
    for (const s of shots) {
      if (only?.length && !only.includes(s.name)) continue;
      const page = await browser.newPage();
      await page.setViewport({ width, height, deviceScaleFactor: Number(opt('scale', 1)) });
      await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: theme }, { name: 'prefers-reduced-motion', value: 'reduce' }]);
      page.on('console', (m) => {
        if (m.type() === 'error') errors.push(`${s.name} ${theme}: ${m.text()}`);
      });
      page.on('pageerror', (e) => errors.push(`${s.name} ${theme}: ${e.message}`));
      const url = base + s.path + (s.path.includes('?') ? '&' : '?') + `theme=${theme}`;
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && !document.querySelector('.skeleton-bar'), { timeout: 15000 }).catch(() => {});
      await new Promise((r) => setTimeout(r, 700));
      if (s.click) {
        await page.click(s.click);
        await new Promise((r) => setTimeout(r, 900));
      }
      if (s.scrollTo || s.scrollBottom) {
        await page.evaluate(
          (top, bottom, off) => {
            const main = document.querySelector('.main');
            if (!main) return;
            const mr = main.getBoundingClientRect();
            if (top) {
              const el = document.querySelector(top);
              if (el) main.scrollTop = el.getBoundingClientRect().top - mr.top + main.scrollTop - off;
            } else if (bottom) {
              const el = document.querySelector(bottom);
              if (el) main.scrollTop = Math.max(0, el.getBoundingClientRect().bottom - mr.bottom + main.scrollTop + off);
            }
          },
          s.scrollTo ?? null,
          s.scrollBottom ?? null,
          s.offset ?? 24,
        );
        await new Promise((r) => setTimeout(r, 300));
      }
      const file = join(out, `${s.name}_${theme}.png`);
      await page.screenshot({ path: file });
      console.log('shot', file);
      await page.close();
    }
  }
} finally {
  await browser.close();
}
if (errors.length) {
  console.log('CONSOLE ERRORS');
  for (const e of errors) console.log(' ', e);
}
