// Local preview and screenshots of site/dist/index.html with headless Chromium.
//
// The sandbox cannot reach Google Fonts, so the preview swaps the Google Fonts link for the same
// families loaded from npm (@fontsource-variable/archivo with its width axis, @fontsource/ibm-plex-mono)
// and wraps the page in the same small skeleton the artifact host adds. The published page keeps
// the Google Fonts link.
//
// Run from the repo root:   node site/shoot.mjs            (all four: 1440 and 400, light and dark)
//                           node site/shoot.mjs --motion   (also a frame from the middle of the hero pass)
// Needs puppeteer-core and @sparticuz/chromium from SHOT_TOOLS (default /home/claude/tools/shot),
// and the font packages installed under site/node_modules (cd site && npm install).

import { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

const STITCH = `
import sys
from PIL import Image
out, parts = sys.argv[1], sys.argv[2:]
ims = [Image.open(p) for p in parts]
w = ims[0].width
canvas = Image.new("RGB", (w, sum(i.height for i in ims)))
y = 0
for i in ims:
    canvas.paste(i, (0, y))
    y += i.height
canvas.save(out, optimize=True)
`;

const here = dirname(fileURLToPath(import.meta.url));
const tools = process.env.SHOT_TOOLS || "/home/claude/tools/shot";
const req = createRequire(join(tools, "package.json"));
const puppeteer = (await import(pathToFileURL(req.resolve("puppeteer-core")).href)).default;
const chromium = (await import(pathToFileURL(req.resolve("@sparticuz/chromium")).href)).default;

const args = process.argv.slice(2);
const motion = args.includes("--motion");
const outDir = join(here, "screenshots");
const previewDir = join(here, ".preview");
mkdirSync(outDir, { recursive: true });
mkdirSync(previewDir, { recursive: true });

const font = (rel) => readFileSync(join(here, "node_modules", rel)).toString("base64");
const faces = `
@font-face { font-family: "Host Grotesk"; font-style: normal; font-weight: 300 800;
  src: url(data:font/woff2;base64,${font("@fontsource-variable/host-grotesk/files/host-grotesk-latin-wght-normal.woff2")}) format("woff2"); }
@font-face { font-family: "Archivo"; font-style: normal; font-weight: 100 900; font-stretch: 62% 125%;
  src: url(data:font/woff2;base64,${font("@fontsource-variable/archivo/files/archivo-latin-wdth-normal.woff2")}) format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400;
  src: url(data:font/woff2;base64,${font("@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2")}) format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 500;
  src: url(data:font/woff2;base64,${font("@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2")}) format("woff2"); }`;

// The host skeleton: charset, viewport with viewport-fit=cover, and the small reset it describes.
const hostReset = `:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}`;

let page = readFileSync(join(here, "dist", "index.html"), "utf8");
page = page.replace(/<link[^>]+fonts\.(googleapis|gstatic)\.com[^>]*>\s*/g, "");
const doc = `<!doctype html><html><head><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><style>${hostReset}</style><style>${faces}</style></head><body>${page}</body></html>`;
const previewPath = join(previewDir, "index.html");
writeFileSync(previewPath, doc);

const browser = await puppeteer.launch({ executablePath: await chromium.executablePath(), args: chromium.args, headless: true });
const configs = [
  { name: "desktop-light", width: 1440, theme: "light" },
  { name: "desktop-dark", width: 1440, theme: "dark" },
  { name: "phone-light", width: 400, theme: "light" },
  { name: "phone-dark", width: 400, theme: "dark" },
];
const external = new Set();
let failed = false;
for (const c of configs) {
  const pg = await browser.newPage();
  pg.on("request", (r) => {
    const u = r.url();
    if (!u.startsWith("file:") && !u.startsWith("data:") && !u.startsWith("blob:")) external.add(u);
  });
  pg.on("pageerror", (e) => { console.log(`[${c.name}] page error: ${e.message}`); failed = true; });
  pg.on("console", (m) => { if (m.type() === "error") { console.log(`[${c.name}] console error: ${m.text()}`); } });
  await pg.setViewport({ width: c.width, height: 900, deviceScaleFactor: 1 });
  await pg.emulateMediaFeatures([
    { name: "prefers-color-scheme", value: c.theme },
    { name: "prefers-reduced-motion", value: "reduce" },
  ]);
  await pg.goto(pathToFileURL(previewPath).href, { waitUntil: "load" });
  await pg.evaluate(() => document.fonts.ready);
  const info = await pg.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    height: document.documentElement.scrollHeight,
    archivo: document.fonts.check('500 68px "Host Grotesk"'),
    plex: document.fonts.check('400 13px "IBM Plex Mono"'),
    wide: [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !el.closest(".frag-scroll, .cat-wrap, pre, .query, .defs"))
      .slice(0, 8)
      .map((el) => `${el.tagName.toLowerCase()}.${el.className && el.className.baseVal === undefined ? el.className : ""} right=${Math.round(el.getBoundingClientRect().right)}`),
  }));
  const overflow = info.scrollWidth > info.innerWidth;
  console.log(`${c.name}: ${info.innerWidth}x${info.height}px, ${(info.height / 900).toFixed(1)} screens, fonts archivo=${info.archivo} plex=${info.plex}, horizontal overflow=${overflow}`);
  if (overflow || info.wide.length) { console.log("  elements past the right edge:", info.wide.join("; ")); }
  if (overflow) failed = true;
  // Full page: grow the viewport to the page height, then capture in bands under 8192px (the
  // compositor tiles anything taller) and stitch them with Pillow.
  await pg.setViewport({ width: c.width, height: info.height, deviceScaleFactor: 1 });
  const parts = [];
  for (let y = 0, i = 0; y < info.height; y += 4000, i++) {
    const p = join(previewDir, `${c.name}-part${i}.png`);
    await pg.screenshot({ path: p, clip: { x: 0, y, width: c.width, height: Math.min(4000, info.height - y) }, captureBeyondViewport: true });
    parts.push(p);
  }
  execFileSync("python3", ["-c", STITCH, join(outDir, `${c.name}.png`), ...parts]);
  await pg.setViewport({ width: c.width, height: c.width < 600 ? 860 : 900, deviceScaleFactor: c.width < 600 ? 2 : 1 });
  await pg.evaluate(() => window.scrollTo(0, 0));
  await pg.screenshot({ path: join(outDir, `${c.name}-top.png`) });
  if (motion && c.name.startsWith("desktop")) {
    await pg.emulateMediaFeatures([{ name: "prefers-color-scheme", value: c.theme }, { name: "prefers-reduced-motion", value: "no-preference" }]);
    await pg.reload({ waitUntil: "load" });
    await pg.evaluate(() => document.fonts.ready);
    await new Promise((r) => setTimeout(r, 1300));
    await pg.screenshot({ path: join(outDir, `${c.name}-motion.png`), clip: { x: 0, y: 0, width: c.width, height: 820 } });
  }
  await pg.close();
}
await browser.close();
if (external.size) {
  console.log("external requests:", [...external].join(", "));
  failed = true;
} else {
  console.log("external requests: none");
}
process.exit(failed ? 1 : 0);
