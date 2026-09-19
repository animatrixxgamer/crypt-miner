# Deploying CryptoMinerPro to a Server

Use the ready-made `crypto-miner-bot.zip`. Everything needed is inside — you do **not** need to recreate files.

## What is in the zip

```
bot.py              # main bot logic
main.py             # entry point (run this)
admin.py            # admin panel
requirements.txt    # Python packages
.env                # YOUR config (token, MongoDB, wallets, admin IDs) — already filled
config/             # constants & settings
models/             # database layer
Procfile            # for Railway/Render
DEPLOYMENT.md       # this guide
```

## Option A — Any VPS / host with Python (Ubuntu, etc.)

```bash
# 1. Upload & unzip
python3 -m zipfile -e crypto-miner-bot.zip .   # or: unzip crypto-miner-bot.zip

# 2. Install dependencies (Python 3.8+)
pip install -r requirements.txt

# 3. Run — .env already has your token
python main.py
```

Keep it running permanently with `screen` or `tmux`:

```bash
screen -S bot
python main.py
# detach: Ctrl+A then D
```

## Option B — Railway

1. New project → **Deploy from GitHub** (or upload the repo), or use `railway up`.
2. Add the files from this zip.
3. Railway auto-detects `Procfile` → `worker: python main.py`.
4. Make sure your `MONGODB_URI` is reachable from Railway's network (MongoDB Atlas: allow `0.0.0.0/0` in Network Access).

## Option C — Render

1. New **Background Worker** → connect your repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python main.py`
4. Set the environment variables from `.env` in Render's dashboard (or keep the `.env` file in the repo).

## "It starts but the bot is silent" — do these checks

The bot now prints **`✅ BOT IS LIVE as @username`** when polling starts, and it clears any leftover webhook automatically.

1. **Message the EXACT username it prints.** If it prints `@CryptoMinerProBot` but you're messaging a different bot, that's the problem.
2. **Use commands with the slash:** `/start`, then `/mine`. The bot only responds to commands and button presses, not plain text.
3. **Only ONE copy of the bot should run.** If you also run it on your PC, stop that one — two copies fight over the token and neither answers.
4. **If it prints "BOT IS LIVE" but still doesn't answer /start**: check the logs right after — a `Conflict` warning means Telegram thinks a webhook or another instance exists.

## Running it inside a Telegram hosting bot (upload .zip → it runs main.py)

If your "server" is actually a Telegram bot that runs uploaded `.zip`/`.py` files as background processes, read this carefully — it's the most common reason the crypto bot "starts but is silent":

1. **The crypto bot's output goes to a hidden log file, not to you.** In the hosting bot go to **📂 Check Files → click the file → 📜 Logs** to see everything the crypto bot printed (MongoDB errors, tracebacks, the `✅ BOT IS LIVE as @...` line). This log is the #1 debugging tool.
2. **The new `main.py` now messages you directly.** When it starts successfully you will receive a Telegram message from the crypto bot: `✅ CryptoMinerPro is LIVE as @CryptoMinerProBot`. If MongoDB is unreachable or polling conflicts, you get a clear ❌ error message instead. If you get *neither* message within ~1 minute, open the Logs button and paste what you see.
3. **The hosting bot does a 5-second pre-check run.** The crypto bot now retries MongoDB (5 attempts) and polling conflicts (5 attempts), so a slow first connection no longer kills it.
4. **Only ONE copy of the crypto bot may run.** If you also run it on your PC, stop that copy — two instances with the same token cause "Conflict" and neither answers. The bot now retries for ~50s, then tells you if that's the problem.
5. **Bots uploaded to a hosting bot die when the hosting process restarts.** This is not a permanent host. For always-on hosting use a VPS / Railway / Render directly (see below).
6. **Force join is now a HARD gate** — every command, button and message is blocked until the user joins your channels/group. Two ways to configure it:
   - **Via the panel (recommended):** `/admin` → **📨 Proof Groups** → **➕ Add Group**. If that group is the only requirement, leave `FORCE_JOIN_CHANNELS` empty in `.env` and users must join the added group before the bot works. Withdrawal proofs and payment screenshots are also posted to that group. This is the "force join = join the payment group" setup.
   - **Or in `.env`:** `ENABLE_FORCE_JOIN=true` + `FORCE_JOIN_CHANNELS=@channel1,@channel2` (public). When set, these channels override the proof-group requirement.
7. **Add private channels/groups with a display NAME + invite link.** A numeric ID alone cannot open a private chat, and an invite link alone shows nothing friendly to users — so when adding in the panel you send the **name users will see**, then the invite link (fields can be in any order, separated by `|`):
   ```
   My Payment Group | https://t.me/+abcDEFghiJk
   ```
   Users then see "📢 Join Required Channels → • My Payment Group → [📢 Join My Payment Group]" and the button opens the hidden invite link.
   - **Add the chat's numeric ID/username too for a HARD membership check** (recommended; bot must be admin):
     ```
     My Payment Group | https://t.me/+abcDEFghiJk | -1001234567890
     ```
   - **Link-only (no ID):** Telegram won't let the bot verify who joined a private chat from just an invite link, so those entries are **tap-confirmed** — the bot records the "✅ I've Joined" press. Channels that DO have an id/@username are still verified server-side on every message and can't be bypassed.
   - Adding the same name/link again **replaces** the old entry. Removal is by tapping the stored name in **➖ Remove**.
   - **Already saved a link-only entry? Upgrade it to auto-verified without re-adding:** the Force Join / Proof Groups screen shows a **🔒 Add ID** row under each link-only entry. Tap it, then **forward any message from that group into the chat** (or send its `-100xxxx` ID / `@username`). The bot reads the chat ID, checks it can see the chat, and from then on membership is verified server-side.
   - **Rename what users see** — raw IDs like `channel -1004416191377` look ugly on the join screen. Each entry now has a **✏️ Rename: …** button — send any name (`Withdrawal Channel`) and the join screen + Join button show it, while the real ID/link stays untouched.
   - **Edit the screen heading/subheading** — the Force Join screen has a **📝 Edit Screen Text** button: send `New Heading | New subheading` (or just the heading). No code changes needed.
   - **Add with just the invite hash** — `https://t.me/+iRM6QuEggH8wYThk` and bare `+iRM6QuEggH8wYThk` are both accepted: `Withdrawal Channel | +iRM6QuEggH8wYThk`. Public `@username` or `https://t.me/name` work alone too.
   - **Tip for an entry that already has an ID but no invite link:** re-add it as `Name | invite link | -100xxxx` — it replaces the old entry and the Join button then opens the real invite link *and* membership is verified server-side.
   - Admins (IDs in `ADMIN_IDS`) bypass the gate; users press "✅ I've Joined" to re-check instantly.
7. **Environment-variable override (THE usual hidden cause):** if the server already exports `BOT_TOKEN` (e.g. a hosting bot's own token), Python's `load_dotenv()` does NOT replace it by default — so your `.env` gets ignored and your bot logs in as the wrong bot, then dies with `409 Conflict`. The code now uses `load_dotenv(override=True)` everywhere so `.env` always wins. If you still see a 409, make sure nothing else polls the same token.

## The #1 reasons "it didn't work on the server"

1. **Old bot token** — if you regenerated the token in BotFather, the OLD token dies instantly and the bot acts dead (buttons stop working). The zip has the NEW token, but only if the process reads `.env`. Verify: `grep BOT_TOKEN .env` and restart the bot.
2. **MongoDB unreachable** — the bot exits at startup if it can't reach `MONGODB_URI`. Test your connection string locally with `python main.py` before uploading.
3. **Missing files** — uploading only `bot.py`/`main.py` won't work; you need `config/`, `models/`, `requirements.txt` and `.env` (all included in the zip).
4. **Wrong ADMIN_IDS** — `/admin` only works for the IDs in `ADMIN_IDS`. Set it to your own Telegram user ID (use @userinfobot to find it).

## Staff roles — owners, managers, moderators

- **Owners** (IDs in `ADMIN_IDS` in `.env`) have full access: everything, incl. editing wallets & managing staff.
- **Managers** — almost-full access: can approve/reject payments & withdrawals, broadcast, airdrop, change settings, manage users, channels & groups. They CANNOT edit wallet addresses or manage staff.
- **Moderators** — view only: they see users, payments, withdrawals & screenshots but cannot approve/reject anything or change settings.

Add people without touching files: `/admin` → **👥 Staff** → **➕ Add Staff** → send `user_id manager` or `user_id moderator` (or `owner` for full access). Find a user's ID via @userinfobot or their ID shown in your admin user views. Staff changes save to `settings.json` and survive restarts. Payment screenshots & withdrawal requests are forwarded to every staff member; only owners & managers see working Approve/Verify buttons.

## Auto-verification of payment screenshots (fake-receipt detection)

After a user sends their payment screenshot, the bot asks them to paste the **transaction hash (TX ID)** from their wallet. It then checks the real blockchain (no API keys needed):

- ✅ **Verified** — the TX exists, went to *your* wallet address, the amount matches the plan (±1%), and it's confirmed → the plan is **auto-activated** and staff get a "🟢 AUTO-VERIFIED" notice.
- ⚠️ **Flagged** — TX not found, wrong recipient, wrong amount, or still unconfirmed → the payment stays on manual review, the user is told, and staff get a "⚠️ CHAIN CHECK FAILED — possible fake receipt" alert with the exact reason.
- 🧾 Screenshots are always forwarded to staff privately (never to groups); nothing is auto-approved on doubt.

Supported coins: **BTC, ETH, DOGE, LTC, BCH, DASH** (BlockCypher) and **USDT-TRC20** (TronScan). USDT-ERC20, SOL and others go straight to manual review. Users can send `skip` to opt out of the check — that also goes to manual review.

## UI font — the small-caps "cool font" ✨

All user-facing messages are rendered in the small-caps Unicode style (ᴅᴜᴇ, ᴛᴏ, ʏᴏᴜʀ…). It's applied automatically to every message a *user* receives. Details that must stay exact are never converted: **slash commands** (`/start`, `/help`, `/balance`… — they stay typed exactly as-is so users can read/copy them), **technical values** (hash-rate units like `12.5 H/s` / `EH/s`, coin tickers, amounts, and TX hashes / BTC-TRX-DOGE addresses — even when they appear outside `code` blocks), links, and @usernames all keep their normal letters so they still work.

**Admins are exempt by default** — but each staff member can switch their own panel to the styled font: `/admin` → **🔤 UI FONT: OFF** → tap it to turn on (✨ ON). The choice is saved per person. Proofs posted to your groups ARE always styled (that's the branding look).

**Painted buttons (Bot API 9.4 button colors)** — inline buttons are now **really painted**: 🟢 green (`success`) = money/actions, 🔴 red (`danger`) = stop/cancel/delete/admin, 🔵 blue (`primary`) = everything else (tools, info, back). The old color-chip emojis are gone from button labels — the button itself carries the color. The old chips 🟢🔵🔴 you may still see on OLD messages disappear the next time that message is re-sent or edited. Works on Telegram clients that support Bot API 9.4+ (buttons stay normal-looking on older clients — the field is ignored). Toggle anytime: open `/admin` and tap the **🎨 BUTTON COLORS: ON/OFF** row right on the main panel, or use `/admin` → ⚙️ Settings → **🎨 Colored Buttons**. Each flip re-paints/reverts the open page on the spot **and shows a confirmation summary with real counts** — e.g. `🎨 Button colors: 🟢 ON — 18 buttons painted (🟢 6 · 🔴 2 · 🔵 10) on this panel`. Set `BUTTON_COLORS=false` in `.env` to start with colors off.

Painting is applied at the bot's serialization layer, so **every** inline keyboard is colored automatically when ON — the animated mining card's STOP MINING, the payment screens' I'VE PAID / CANCEL, proofs, panels, everything. To pin a specific button's color (works even when the global toggle is OFF, e.g. keep STOP red while everything else is plain), open `/admin` → ⚙️ Settings → **🖌️ Customize Buttons** and tap the row for that button — it cycles Auto → 🟢 Green → 🔴 Red → 🔵 Blue and saves to `settings.json`.

**UI STYLE picker** — `/admin` → **🎨 UI STYLE** lets you choose the global user-facing font and turn it off:
- 🅰️ **Small Caps** — the Unicode font (default)
- 🅱️ **Title Case** — clean look (`Your Files Will Be Deleted`), keeps real acronyms like USDT/VIP
- ⬜ **Plain (off)** — normal text

Applies instantly to every user message and saves automatically. Your own panel stays plain unless you switch **👤 MY PANEL FONT** in the same menu.

**Animated mining cards** — while a user mines, the card now refreshes every 2 seconds with a spinning ⣾ loader, pulsing header dots, a live hash-rate bar (▰▱) and a ticking elapsed timer, on top of the normal reward ticks. Tap ⏹ Stop Mining to end it.

**Real live crypto prices 🔵 LIVE** — proofs sent to your groups (auto + withdrawal) now use the **actual current market price** from CoinGecko (no API key needed), so the coin amounts really match the USD values shown (e.g. DOGE at its real ~$0.11 rate, not a stale guess). Each proof shows a `💹 DOGE Price: $0.1130 🔵 LIVE` line. Prices refresh automatically before every proof send and are cached ~90s; deposit amounts shown to users use live prices too. If CoinGecko is unreachable the bot silently falls back to the manual prices in `/admin` → Coin Prices (where you'll now see the live value + a 🔄 Refresh Live button).

## After starting — quick checks

- Send `/start` to your bot in Telegram (from a **non-admin** account too — that was failing before with `DuplicateKeyError: dup key: { userId: null }`).
- You should see the menu with MINE / BALANCE / UPGRADE / REFERRAL / STATS / DAILY / ACHIEVEMENTS / BOOSTS / HELP / WITHDRAW / MY PLAN / NETWORK.
- Click **STATS** and **BOOSTS** — the current message should update in place.
- **`E11000 duplicate key ... index: referralCode_1 / userId_1 dup key: { field: null }`** (when a *new* user presses /start) is auto-fixed: the bot now drops **every** leftover unique index on the `users` collection that isn't the `user_id` one (`userId`, `referralCode`, `referral_code`, …) on every boot, then re-ensures the correct `user_id` unique index. If you still see it after deploying once, drop those indexes manually once in MongoDB (or delete the old `users` collection if it only holds test data).
- If users are stuck on "Join Required Channels", the bot can't verify the channel in `force_join_channels`. Open `/admin` → Force Join and disable it, or make the bot an admin of that channel.

## Auto-proofs stopping after a few days? 🔧

The fake-proof scheduler used to be a fragile self-rescheduling chain: a single unhandled error inside proof generation silently killed it forever. Now:

- The chain **re-arms itself first** and generation is wrapped in try/except — no failure (CoinGecko hiccup, Telegram error, bad group entry) can ever stop it.
- An **hourly watchdog** checks the chain is still armed and re-arms it if it ever died (host restart, dropped job).
- If a proof group was saved link-only (no numeric ID), sending to it is impossible — the bot now logs a clear `no chat id` warning instead of failing silently. Open `/admin` → 📨 Proof Groups → 🔒 Add ID for that entry so auto-proofs reach it.
- Every send now logs a summary line: `Auto-proof: X/Y groups sent (...)` — check the server console to confirm.