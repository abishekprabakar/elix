# Saturday morning checklist

Do these in order. Every command says where to run it. Commands assume a Mac with Python 3.11 or newer (`python3 --version` to check).

## 1. Get it running on your laptop (10 minutes)

Download `elix.zip` from the chat, then in Terminal run this one command. It unzips into your Downloads folder, makes a virtual environment and installs Elix.

```bash
cd ~/Downloads && unzip -o elix.zip -d elix && cd elix && conda create -y -n elix python=3.12 && conda activate elix && pip install -e .
```

Then start the demo from the same Terminal window.

```bash
python -m elix demo
```

Open http://127.0.0.1:8400 for the console, which is the product you demo. The marketing site is at http://127.0.0.1:8400/site, and the same site is published at the claude.ai link in the chat. The console is already built, so you do not need npm. Click through Scenarios and run "Lumen bills hours Forge already paid for" once so you have seen the moment before anyone else does.

Every new Terminal window needs `cd ~/Downloads/elix && conda activate elix` before any `python -m elix` command. After unzipping a newer version, run `pip install -e .` once in that window so new dependencies install.

## 2. Real Claude agents (10 minutes)

1. Claim the Anthropic API credits from the link pinned in the #cohost-anthropic Slack channel, then create an API key at console.anthropic.com.
2. In a second Terminal window, run this, pasting your key in place of the placeholder.

```bash
cd ~/Downloads/elix && source .venv/bin/activate && echo "ANTHROPIC_API_KEY=sk-ant-PASTE-YOURS" >> .env
```

3. Stop the demo in the first window with Ctrl+C and start it again with `python -m elix demo`. On the Agents screen, run Scout's default task. The session should say Claude instead of Scripted agent. Scout will read Signalboard's catalog, which contains a line claiming orders are pre-approved, and Elix should hold the order anyway.

## 3. Live x402 on Base Sepolia (20 minutes, optional but worth it for the Coinbase track)

1. Make a fresh test wallet. Run this in the Elix Terminal window.

```bash
python -c "from eth_account import Account; a=Account.create(); print('address', a.address); print('key', '0x'+a.key.hex().removeprefix('0x'))"
```

2. Go to faucet.circle.com, pick Base Sepolia and USDC, paste the address, and request test USDC. You do not need ETH, because the facilitator submits the transaction.
3. Optional. If you would rather sign with a CDP server wallet than a raw key, make a Coinbase Developer Platform account at portal.cdp.coinbase.com, create an API key, run `pip install cdp-sdk`, and set the three `CDP_` variables from `.env.example` instead of `ELIX_WALLET_PRIVATE_KEY`. That path is written against the SDK but has never been run, so only mention CDP on Devpost if it works for you.
4. Add the wallet to `.env` and switch to live mode.

```bash
cd ~/Downloads/elix && echo "ELIX_RAIL_MODE=live" >> .env && echo "ELIX_WALLET_PRIVATE_KEY=PASTE_THE_KEY_IT_PRINTED" >> .env
```

5. Run the live test from the same window. The first command checks your setup without paying. The second pays $0.40 for one Signalboard record on Base Sepolia.

```bash
python -m elix.rails.live_check --dry-run && python -m elix.rails.live_check
```

6. Open the Basescan link it prints and screenshot it for Devpost. If it worked, add base-sepolia to Built with on Devpost and mention the live transaction in the Coinbase write up. If anything fails, remove the `ELIX_RAIL_MODE=live` line from `.env` and demo offline. Offline is fully labeled and is what the judges will see anyway.

Before judging, switch back to offline so the demo cannot fail on the venue wifi.

## 4. The video (10 to 30 minutes)

A captioned 97 second draft is already in the repo at `docs/elix-demo.mp4`. It was recorded from the real console and opens with the Lumen double bill. You can upload it as is to YouTube as unlisted and put the link on Devpost.

If you have time, record a version with your voice. Follow the 90 second shot list in `docs/demo-script.md`, use QuickTime (File, New Screen Recording) with the browser full screen, and open with the Lumen double bill rather than a title card.

## 5. Devpost draft, Saturday night at the latest

1. Create the project on the Cal Hacks 13.0 Devpost page and paste each section from `docs/devpost.md`.
2. Tag Hyphenate, Coinbase x402 Payment Agents, Microsoft Built for the Real World and MLH Commit Fellowship. Fill in the MLH supplemental form.
3. Add the video link, the GitHub link, three console screenshots from `console/screenshots/` and the site link.
4. Save as a draft. Submit before Sunday 9:00 AM, ideally by midnight.

## 6. Push to GitHub

Create an empty public repository named `elix` on github.com under your account (no README, no license), then run this in Terminal.

```bash
cd ~/Downloads/elix && git init && git add . && git commit -m "Elix, accounts payable for x402" && git branch -M main && git remote add origin https://github.com/abishekprabakar/elix.git && git push -u origin main
```

Run `git status` first and make sure `.env` is not listed. It is in `.gitignore`, but check anyway.

## 7. Booth conversations, Saturday

Keep the console open on your laptop and lead with the Lumen invoice every time.

- Hyphenate booth. Say the seats line, show the double bill, then let them try a custom scenario. Ask how they would want proof presented, and scan their intern QR code.
- Microsoft workshop, Saturday 10:00 AM in the Theatre. Go, and ask one question about taking an agent from prototype to production in finance.
- Coinbase booth. Show the x402 receipt panel and ask whether they would rather see a live Base Sepolia transaction or the labeled offline flow for judging. Tell them about the $1 default spend cap and the missing offline facilitator, since builder feedback is scored.
- a16z table on Broadway near the main steps. Lead with the thesis that agents now pay on rails finance cannot see. Ask what they would need to see from a design partner pilot.
- Amazon recruiters at the Annapurna booth (booth 12). Ask about SDE internships and join their talent community.
- Y Combinator. Ask how to get on the list for their afterparty.
- Every AI sponsor rep. Ask how their company tracks model, GPU and API spend by team today, who approves it, and whether they have ever been billed twice for the same usage. Write the answers down. These are your first customer interviews.
