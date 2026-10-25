# Site notes

The marketing page for Elix, published as a claude.ai Artifact. One self-contained HTML file, about 1.5 MB.

## Build

From the repo root:

```
python site/build.py            # final build, fails on anything missing
python site/build.py --draft    # builds with placeholder images and values
python site/capture.py          # optional, sharp 2x screenshots that match the hero values (see below)
```

Output is `site/dist/index.html`. The build

- inlines console screenshots as WebP data URIs. Crops, widths and quality per image are in `IMAGES` in
  `build.py`. Optimized copies and a sidecar with size and source land in `site/assets/`.
- fills `{{eval:...}}` numbers and the per category table from `eval/report.json`, and refuses a report
  from a `--fast` run.
- fills `{{world:...}}` hero values (WI id, task ref, purpose, the checks sentence, tx ref, JE id, Lumen
  pay to address) by reading the Oct 9 Forge purchase through the engine facade, from a sqlite backup
  copy of `data/elix.db` so nothing is written. Without a seeded world it falls back to the spec 07
  example copy with a note.
- generates the guilloche paths and resolves `{{repo_url}}` (`--repo-url`, `ELIX_REPO_URL`, git remote,
  then `https://github.com/abishekprabakar/elix`).
- checks the artifact contract (no html/head/body, title first, one style, one inline script, no external
  requests other than Google Fonts, no fetch/alert/confirm/prompt/print/window.open, no iframes, no
  `<a download>`, no form action, external links open in a new tab, under 8 MB) and the copy rules
  (no em or en dashes anywhere in the file, no arrows, no colons in prose, no all caps words outside an
  acronym allowlist, no filler words).

A final build fails when a screenshot is still a placeholder, the eval report or a key is missing, the
report is from a fast run, or any placeholder is left. `--draft` turns those into warnings.

## Screenshot sources

1. `site/assets/source/*.png` from `python site/capture.py`. It seeds a private world on free ports,
   runs the demo scenarios through the API, has Dev approve the Stormvault spend, takes today,
   reconcile, item_payee_hold and audit_why in both themes with `console/scripts/shots.mjs --scale 2`,
   and writes `capture.json` with the hero values from that same world and the sha256 of
   `console/dist/index.html`. Used only while that hash still matches the console build.
2. Otherwise `console/screenshots/*.png` (1440x900 at scale 1), with hero values from `data/elix.db`.
   Those two can come from different seeds, so tx refs on the page and in the screenshots may differ.
   Rerun `capture.py` after the console changes.

The page uses whole screens on desktop where they read near 1:1 (Today), crops inside a card where one
card is the point (the LUM-2026-10A invoice, the Why report), a narrow crop beside the rule list (the
payee hold story and checks), and tighter phone crops for Today, Reconcile and Why below 560 px.

## Local screenshots of the page

```
cd site && npm install          # once, for the local font packages
node site/shoot.mjs             # from the repo root
node site/shoot.mjs --motion    # also captures a frame from the middle of the hero pass
```

Google Fonts is blocked in the sandbox, so `shoot.mjs` writes `site/.preview/index.html` with the Google
Fonts link swapped for Archivo (with its width axis) and IBM Plex Mono from npm, wrapped in the skeleton
the artifact host adds. It renders 1440 and 400 wide in light and dark with reduced motion (the page at
rest), reports horizontal overflow and any non local request, and saves
`site/screenshots/{desktop,phone}-{light,dark}.png`, `-top.png` first screens and `-motion.png` frames.

## Page

- Tokens, type and components follow `specs/08`. The design plan with wireframes is the comment at the
  top of the style block.
- One motion moment: an elixir pulse runs down the wire panel once on load (about 3 seconds) and on
  Replay. Skipped under reduced motion. The panel is complete at rest.
- Screenshots are two or four images per figure, toggled by the `--show-light` and `--show-dark` tokens
  and a 560 px media query, so they follow the OS theme, the host's `data-theme` toggle and phone width.
- The pilot form never submits anywhere. With the artifact `db` capability declared by the publisher
  (`capabilities: {db: {}}`) it adds a document to the `pilot_requests` collection. Without it the page
  says plainly that nothing was sent. The db capability only lets Contributor level viewers and up write,
  and outside visitors on a public link never can, so for them the form falls back to the plain message.
  A rule like `{path: "pilot_requests", read: "admin", write: "interact"}` keeps entries private to editors.
- Desktop length is about 9.3 screens at 1440x900, over the spec's "about eight", because the product
  shots are shown large. The quickest cut if needed is the Why report image.

## Known gaps

- No photography. The imagery is the real product. There was no demo video; a click to play capture
  of Sam approving the short pay would be the next addition.
- Live Base Sepolia mode is not shown on the page; everything shown is offline mode and labeled.
