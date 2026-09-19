"""
CryptoMinerPro — Main Telegram Bot
All user-facing commands, mining engine, withdrawal proofs.
Optimized for fast async response.
"""
import os
import random
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ExtBot,
)

from models import connect_db, get_db
from models.user import User
from models.withdrawal import Withdrawal
from models.payment_request import PaymentRequest
from models.mining_session import MiningSession
from models.referral import Referral
from models.pool import Pool
from config.constants import (
    UPGRADE_PLANS, PLAN_ORDER, COIN_MULTIPLIERS, COIN_PRICES, COIN_INFO,
    DEFAULT_SETTINGS, NEWS_MESSAGES, SUPPORTED_CRYPTOS,
    fmt, fmt_dur, p_icon, c_emoji, p_bar, ref_tier, calc_reward,
    generate_fake_tx, generate_proof_id, get_wallet,
    entry_ref, entry_link, entry_label,
    is_staff, all_staff_ids, styled, title_text, market_price,
    paint_button_label,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_NAME = os.getenv("BOT_NAME", "CryptoMinerPro")
BOT_USERNAME = os.getenv("BOT_USERNAME", "CryptoMinerProBot")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
TOTAL_USERS_DISPLAY = int(os.getenv("TOTAL_USERS_DISPLAY", "1464000"))

# User session store (in-memory for speed)
user_sessions = {}  # user_id -> {mining_interval, mining_data, state, temp_data}

# ── Simple user cache (avoids DB hit on every callback) ──
_user_cache = {}  # user_id -> {"user": User, "ts": timestamp}
_CACHE_TTL = 10  # seconds


def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {"mining_interval": None, "mining_data": None, "state": None, "temp_data": None}
    return user_sessions[user_id]


def is_admin(user_id):
    return is_staff(user_id)


def clear_state(user_id):
    s = get_session(user_id)
    s["state"] = None
    s["temp_data"] = None


async def cached_user(user_id: int) -> User:
    """Get user with in-memory cache to avoid repeated DB hits."""
    import time
    now = time.time()
    entry = _user_cache.get(user_id)
    if entry and (now - entry["ts"]) < _CACHE_TTL:
        return entry["user"]
    user = await User.find_or_create(user_id)
    _user_cache[user_id] = {"user": user, "ts": now}
    return user


def invalidate_user_cache(user_id: int):
    _user_cache.pop(user_id, None)


async def _send_or_edit(update, text, parse_mode="Markdown", reply_markup=None):
    """Reply when called from a text command, edit when called from a button."""
    if update.callback_query is not None:
        return await update.callback_query.edit_message_text(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    return await update.message.reply_text(
        text, parse_mode=parse_mode, reply_markup=reply_markup
    )


def main_keyboard(user_id):
    """Color-coded two-column menu — 🟢 money/actions first, then 🔵 tools.
    Mirrors the classic control-panel grid: green rows for money actions,
    blue for info, full-width red for the admin panel."""
    buttons = [
        [InlineKeyboardButton("🟢 🔥 MINE", callback_data="cmd_mine"),
         InlineKeyboardButton("🟢 💲 BALANCE", callback_data="cmd_balance")],
        [InlineKeyboardButton("🟢 ⭐ UPGRADE", callback_data="cmd_upgrade"),
         InlineKeyboardButton("🟢 💰 WITHDRAW", callback_data="cmd_withdraw")],
        [InlineKeyboardButton("🔵 👥 REFERRAL", callback_data="cmd_referral"),
         InlineKeyboardButton("🔵 📊 STATS", callback_data="cmd_stats")],
        [InlineKeyboardButton("🟡 🔖 DAILY", callback_data="cmd_daily"),
         InlineKeyboardButton("🔵 🏆 ACHIEVEMENTS", callback_data="cmd_achievements")],
        [InlineKeyboardButton("🟣 ⚡ BOOSTS", callback_data="cmd_boost"),
         InlineKeyboardButton("🔵 ℹ️ HELP", callback_data="cmd_help")],
        [InlineKeyboardButton("🔵 💳 MY PLAN", callback_data="cmd_myplan"),
         InlineKeyboardButton("🔵 🌐 NETWORK", callback_data="cmd_network")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("🔴 👑 ADMIN PANEL", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log any handler exception AND tell the user, so failures are never silent."""
    err = context.error
    # Harmless race: double-tapping a button re-edits a message with identical
    # content → Telegram 400 "Message is not modified". Not an error — ignore it.
    if "message is not modified" in str(err).lower():
        logger.info("Ignored harmless 'Message is not modified' (double-tap / repeat edit)")
        if update and update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        return
    logger.error("Exception while handling an update:", exc_info=err)
    try:
        if update and update.effective_user:
            await context.bot.send_message(
                update.effective_user.id,
                f"❌ *An error occurred.*\n\n`{type(err).__name__}: {err}`",
                parse_mode="Markdown",
            )
            if update.callback_query:
                try:
                    await update.callback_query.answer()
                except Exception:
                    pass
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# FORCE JOIN CHECK — gates EVERY command, button and message
# ═══════════════════════════════════════════════════════════════
_force_join_last_sent = {}  # user_id -> timestamp (avoids spamming the prompt)


def _channel_link(ch):
    """Join link for a channel entry (handles private chats via their invite link)."""
    return entry_link(ch)


def _fj_join_label(ch):
    """Friendly name shown on the Join button (link is hidden from the user)."""
    if isinstance(ch, dict):
        return (ch.get("name") or "Channel").strip() or "Channel"
    if isinstance(ch, str):
        return ch if ch.startswith("@") else "Channel"
    return "Channel"


async def _send_force_join_prompt(bot, user_id, not_joined):
    """Send the 'join required' screen (throttled to 1 per 20s per user)."""
    import time
    now = time.time()
    if now - _force_join_last_sent.get(user_id, 0) < 20:
        return False
    _force_join_last_sent[user_id] = now
    buttons = []
    ch_list = []
    for ch in not_joined:
        lbl = entry_label(ch)
        ch_list.append(f"• {lbl}")
        buttons.append([InlineKeyboardButton(f"📢 Join {_fj_join_label(ch)}", url=_channel_link(ch))])
    buttons.append([InlineKeyboardButton("🟢 ✅ I'VE JOINED", callback_data="force_join_check")])
    ch_block = "\n".join(ch_list)
    title = DEFAULT_SETTINGS.get("force_join_title") or "Join Required Channels"
    subtitle = DEFAULT_SETTINGS.get("force_join_subtitle") or \
        "You must join the following channels to use this bot:"
    await bot.send_message(
        user_id,
        f"📢 *{title}*\n\n{subtitle}\n\n"
        f"{ch_block}\n\nAfter joining, click *I've Joined*.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return True


async def _fj_user_confirmed(user_id) -> bool:
    """True if the user tapped 'I've Joined' (needed for invite-only groups,
    which Telegram lets bots link to but never query membership for)."""
    try:
        user = await cached_user(user_id)
        return bool((user._d or {}).get("force_join_ok"))
    except Exception:
        return False


async def _fj_mark_confirmed(user_id):
    try:
        user = await cached_user(user_id)
        user._d["force_join_ok"] = True
        await user.save()
    except Exception as e:
        logger.error(f"force-join confirm save error: {e}")


async def check_force_join(user_id, context) -> bool:
    """Check if user has joined required channels. Returns True if OK.

    Entries with an id/@username are verified via Telegram membership.
    Entries stored with ONLY an invite link (private chat, no id) cannot be
    queried, so for those the bot trusts the user's 'I've Joined' tap.
    """
    if not DEFAULT_SETTINGS.get("force_join_enabled"):
        return True
    channels = DEFAULT_SETTINGS.get("force_join_channels", [])
    if not channels:
        # No channels set → require joining the proof/payment group instead
        channels = DEFAULT_SETTINGS.get("proof_groups", [])
    if not channels:
        return True

    verifiable = [ch for ch in channels if entry_ref(ch) is not None]
    link_only = [ch for ch in channels if entry_ref(ch) is None]

    async def _member_ok(ch):
        ref = entry_ref(ch)
        try:
            member = await context.bot.get_chat_member(ref, user_id)
            return member.status not in ("left", "kicked")
        except Exception:
            return False

    # Check ALL verifiable channels in parallel — huge speedup
    if verifiable:
        results = await asyncio.gather(*[_member_ok(ch) for ch in verifiable])
    else:
        results = []
    not_joined = [ch for ch, ok in zip(verifiable, results) if not ok]

    if link_only and not await _fj_user_confirmed(user_id):
        not_joined.extend(link_only)

    if not not_joined:
        return True
    await _send_force_join_prompt(context.bot, user_id, not_joined)
    return False


class _BotCtx:
    """Minimal stand-in for a ContextTypes object (only .bot is used)."""
    __slots__ = ("bot",)

    def __init__(self, bot):
        self.bot = bot


class ForceJoinApplication(Application):
    """Application that blocks EVERY update until the user joins the channels."""

    async def process_update(self, update: object) -> None:
        try:
            u = update if isinstance(update, Update) else None
            if u is not None:
                user = u.effective_user
                recheck_click = bool(u.callback_query and u.callback_query.data == "force_join_check")
                if (
                    user is not None
                    and not is_staff(user.id)
                    and u.channel_post is None
                    and not recheck_click
                    and not await check_force_join(user.id, _BotCtx(self.bot))
                ):
                    return  # blocked — the join prompt was sent
        except Exception as e:
            logger.error(f"Force-join gate error: {e}")
        await super().process_update(update)


# ═══════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "MINER"
    username = update.effective_user.username

    # Referral check
    referred_by = None
    payload = context.args[0] if context.args else None
    if payload and payload.startswith("ref_"):
        referred_by = payload

    user = await User.find_one(user_id)
    is_new = user is None

    if is_new:
        user = await User.find_or_create(user_id, {"first_name": first_name, "username": username})
        logger.info(f"New user: {user_id} (@{username})")
        if referred_by:
            referrer = await User.find_one(0)  # placeholder
            # Find referrer by code
            db = get_db()
            ref_doc = await db["users"].find_one({"referral_code": referred_by})
            if ref_doc and ref_doc["user_id"] != user_id:
                referrer = User(ref_doc)
                referrer.referral_count += 1
                referrer.balance += 20
                referrer.total_mined += 20
                await referrer.save()
                await Referral.create(referrer.user_id, user_id, username)
                try:
                    await context.bot.send_message(
                        referrer.user_id,
                        f"🎉 *New Referral!*\n\n👤 {first_name} joined via your link!\n💰 +20 coins bonus credited!",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
    else:
        if username:
            user._d["username"] = username
        if first_name:
            user._d["first_name"] = first_name
        await user.save()

    if user.banned:
        return await update.message.reply_text(
            f"🚫 *Account Suspended*\n\nReason: {user.ban_reason or 'Violation of terms'}\nContact @{ADMIN_USERNAME} for appeals.",
            parse_mode="Markdown",
        )

    # Check force join
    if not await check_force_join(user_id, context):
        return

    effective_hr = user.calculate_effective_hash_rate()
    rank = await user.get_rank()

    msg = (
        f"📰 *{BOT_NAME}*\n\n"
        f"🔥 Welcome, *{first_name.upper()}*! {p_icon(user.plan)}\n\n"
        f"📈 Hash Rate: *{effective_hr:.1f}* H/s\n"
        f"💰 Balance: *{fmt(user.balance)}* {user.mining_coin}\n"
        f"📦 Plan: {p_icon(user.plan)} *{user.plan}*\n"
        f"🏆 Rank: *#{rank:,}* of *{TOTAL_USERS_DISPLAY:,}* users\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 *Quick Stats*\n"
        f"  Today: *{fmt(user.daily_mined)}* | Week: *{fmt(user.weekly_mined)}*\n"
        f"  All Time: *{fmt(user.total_mined)}* | Referrals: *{user.referral_count}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    if user.plan == "Free":
        msg += f"🚀 *BUY A PLAN* to boost your mining!\n   🆓 Free (1 H/s) → ⭐ Starter: 5 H/s | 💎 Pro: 25 H/s | 👑 Elite: 100 H/s\n"
    else:
        msg += f"{p_icon(user.plan)} *{user.plan} Plan Active* — Keep mining!\n"

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_keyboard(user_id))


# ═══════════════════════════════════════════════════════════════
# /mine
# ═══════════════════════════════════════════════════════════════
async def mine_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    s = get_session(user_id)

    if s["mining_interval"]:
        return await update.message.reply_text("⛏️ *Already mining!* Use /stopmine first.", parse_mode="Markdown")

    if not await check_force_join(user_id, context):
        return

    user = await cached_user(user_id)
    if user.banned:
        return await update.message.reply_text("🚫 Account suspended.")

    hash_rate = user.calculate_effective_hash_rate()
    user.sessions += 1
    user._d["last_mine"] = datetime.now(timezone.utc)
    await user.save()

    sess = await MiningSession.create(user_id, user.username, hash_rate)

    md = {
        "start_time": datetime.now(timezone.utc),
        "blocks_found": 0,
        "total_mined": 0.0,
        "last_reward": 0.0,
        "last_event": None,
        "message_id": None,
        "session_id": sess.id,
        "coin": user.mining_coin,
        "hash_rate": hash_rate,
    }

    msg_text = _mining_msg(md, hash_rate, user.balance)
    msg = await update.message.reply_text(
        msg_text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 ⏹️ STOP MINING", callback_data="stopmine")]])
    )
    md["message_id"] = msg.message_id
    md["chat_id"] = msg.chat.id
    s["mining_data"] = md

    # Start mining loop
    interval = DEFAULT_SETTINGS["mining_interval_seconds"]

    async def mining_tick():
        try:
            user = await User.find_one(user_id)
            if not user or user.banned:
                return
            reward = calc_reward(md["hash_rate"], md["coin"])
            event_text = None

            roll = random.random()
            if roll < DEFAULT_SETTINGS["mega_bonus_chance"]:
                reward *= 10
                event_text = "🌟 MEGA BONUS! 10x multiplier!"
            elif roll < DEFAULT_SETTINGS["super_bonus_chance"] + DEFAULT_SETTINGS["mega_bonus_chance"]:
                reward *= 5
                event_text = "🦄 SUPER BONUS! 5x multiplier!"
            elif roll < DEFAULT_SETTINGS["bonus_block_chance"] + DEFAULT_SETTINGS["super_bonus_chance"] + DEFAULT_SETTINGS["mega_bonus_chance"]:
                reward *= 2
                event_text = "🎉 BONUS BLOCK! 2x multiplier!"

            user.add_mining_reward(reward)
            user.calculate_effective_hash_rate()
            await user.save()

            md["blocks_found"] += 1
            md["total_mined"] += reward
            md["last_reward"] = reward
            md["last_event"] = event_text
        except Exception as e:
            logger.error(f"Mining tick error: {e}")

    s["mining_interval"] = context.job_queue.run_repeating(
        mining_tick, interval=interval, first=interval
    )

    async def card_tick():
        await _mining_card_tick(context, user_id, s)

    s["mining_card"] = context.job_queue.run_repeating(card_tick, interval=2, first=2)
    logger.info(f"Mining started: {user_id}")


_SPINNER = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]  # braille spinner frames


def _mining_msg(d, hash_rate, balance):
    """Animated mining card — spinner + pulsing header dots + live hash bar.
    Called every few seconds by the card refresher, so it looks alive."""
    frame = int(d.get("frame", 0))
    elapsed = (datetime.now(timezone.utc) - d["start_time"]).total_seconds()
    coin = d.get("coin", "coins")
    target = hash_rate * 10 or 100
    pct = min(100, int((d["total_mined"] / target) * 100))
    spin = _SPINNER[frame % len(_SPINNER)]
    dots = "." * (frame % 3 + 1)
    # hash-rate level pulses ▰▱ as the card refreshes
    bar_n = [3, 5, 7, 6, 4, 6][frame % 6]
    hash_bar = ("▰" * bar_n).ljust(8, "▱")
    lines = [
        f"╔══════════════════════════════╗",
        f"║  {spin} *MINING IN PROGRESS{dots}*   ║",
        f"╚══════════════════════════════╝",
        f"",
        f"⚡ Hash: [{hash_bar}] *{hash_rate:.1f}* H/s",
        f"🏆 Blocks: *{d['blocks_found']}*   🎁 Last: *+{fmt(d['last_reward'])}* {coin}",
        f"💰 Balance: *{fmt(balance)}* {coin}",
        f"📈 Session: *+{fmt(d['total_mined'])}* {coin}",
        f"⏱️ Elapsed: *{fmt_dur(elapsed)}*",
        f"📊 Progress: [{p_bar(pct)}] {pct}%",
    ]
    if d.get("last_event"):
        lines += ["", d["last_event"]]
    return "\n".join(lines)


async def _mining_card_tick(context, user_id, s):
    """Live refresher: re-render the mining card every ~2s (no new reward)."""
    try:
        if not s.get("mining_interval"):
            return
        md = s.get("mining_data")
        if not md or not md.get("message_id"):
            return
        md["frame"] = int(md.get("frame", 0)) + 1
        u = await User.find_one(user_id)
        balance = u.balance if u else 0
        await context.bot.edit_message_text(
            chat_id=md.get("chat_id") or user_id,
            message_id=md["message_id"],
            text=_mining_msg(md, md.get("hash_rate", 1), balance),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 ⏹️ STOP MINING", callback_data="stopmine")]]),
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# /stopmine
# ═══════════════════════════════════════════════════════════════
async def stopmine_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    s = get_session(user_id)

    if not s["mining_interval"]:
        return await update.message.reply_text("⛏️ You are not currently mining.")

    job = s["mining_interval"]
    job.schedule_removal()
    s["mining_interval"] = None
    card = s.get("mining_card")
    if card:
        try:
            card.schedule_removal()
        except Exception:
            pass
    s["mining_card"] = None

    md = s["mining_data"]
    if not md:
        return await update.message.reply_text("Mining stopped.")

    dur = int((datetime.now(timezone.utc) - md["start_time"]).total_seconds())

    try:
        sess = MiningSession(md["session_id"])
        await sess.end_session(md["total_mined"], md["blocks_found"], dur)
    except Exception:
        pass

    await cached_user(user_id)  # ensure exists
    db = get_db()
    await db["users"].update_one({"user_id": user_id}, {"$inc": {"total_mining_time": dur}})

    summary = (
        f"⛏️ *Mining Session Ended*\n\n"
        f"⏱️ Duration: *{fmt_dur(dur)}*\n"
        f"🏆 Blocks Found: *{md['blocks_found']}*\n"
        f"💰 Total Mined: *{fmt(md['total_mined'])} {md['coin']}*\n"
        f"⚡ Hash Rate: *{md['hash_rate']} H/s*\n\n"
        f"Use /mine to start again!"
    )

    s["mining_data"] = None

    await update.message.reply_text(
        summary, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⛏️ MINE AGAIN", callback_data="cmd_mine"),
             InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]
        ])
    )


# ═══════════════════════════════════════════════════════════════
# /withdraw
# ═══════════════════════════════════════════════════════════════
async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)

    if not await check_force_join(user_id, context):
        return

    user = await cached_user(user_id)
    if user.banned:
        return await update.message.reply_text("🚫 Account suspended.")

    if user.plan == "Free":
        return await update.message.reply_text(
            "🚫 *Withdrawal Restricted*\n\n❌ You need a *paid plan* to withdraw.\n📦 Upgrade with /upgrade to unlock.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬆️ UPGRADE", callback_data="cmd_upgrade")]])
        )

    if user.balance < DEFAULT_SETTINGS["min_withdrawal"]:
        return await _send_or_edit(update,
            f"💸 *Withdrawal*\n\n❌ Insufficient balance.\nMinimum: *{DEFAULT_SETTINGS['min_withdrawal']} coins*\nYour balance: *{fmt(user.balance)}*",
            parse_mode="Markdown"
        )

    s = get_session(user_id)
    s["state"] = "awaiting_withdraw_amount"
    s["temp_data"] = {}

    await update.message.reply_text(
        f"💸 *Withdrawal*\n\nYour balance: *{fmt(user.balance)} coins*\nMinimum: *{DEFAULT_SETTINGS['min_withdrawal']} coins*\n\n💰 Enter the amount to withdraw:",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════
# /switch
# ═══════════════════════════════════════════════════════════════
async def switch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)

    text = f"🪙 *Switch Mining Coin*\n\nCurrent: *{c_emoji(user.mining_coin)} {user.mining_coin}*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for c in SUPPORTED_CRYPTOS:
        info = COIN_INFO[c]
        mult = COIN_MULTIPLIERS.get(c, 1)
        is_current = user.mining_coin == c
        text += f"{info['emoji']} *{info['name']}* ({c}) — *{mult}x*\n"
        text += f"  💵 ~${market_price(c):,.2f} | Mult: {mult}x"
        if is_current:
            text += " ✅"
        text += "\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n💡 Higher multiplier = more coins per block!"

    buttons = []
    row1 = [InlineKeyboardButton(f"{c_emoji(c)} {c}", callback_data=f"sw_{c}") for c in SUPPORTED_CRYPTOS[:3]]
    row2 = [InlineKeyboardButton(f"{c_emoji(c)} {c}", callback_data=f"sw_{c}") for c in SUPPORTED_CRYPTOS[3:]]
    buttons = [row1, row2, [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


# ═══════════════════════════════════════════════════════════════
# /upgrade
# ═══════════════════════════════════════════════════════════════
async def upgrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)

    text = f"⬆️ *BUY A PLAN*\n\nYour current plan: {p_icon(user.plan)} *{user.plan}*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for name in PLAN_ORDER[1:]:
        p = UPGRADE_PLANS[name]
        is_current = user.plan == name
        text += f"\n{p['icon']} *{p['name']}*{' ✅ YOURS' if is_current else ''}\n"
        text += f"  💰 *{p['price']} {p['coin']}*\n"
        text += f"  ⚡ *{p['hash_rate']} H/s* | *{p['multiplier']}x* multiplier\n"
        text += f"  ✅ {', '.join(p['features'])}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n💡 *Pay with crypto or coins!*\n🔒 *Locked* = lower tier than current"

    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ STARTER", callback_data="upg_Starter"),
             InlineKeyboardButton("💎 PRO", callback_data="upg_Pro")],
            [InlineKeyboardButton("👑 ELITE", callback_data="upg_Elite"),
             InlineKeyboardButton("🚀 VIP", callback_data="upg_VIP")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")],
        ])
    )


# ═══════════════════════════════════════════════════════════════
# /referral
# ═══════════════════════════════════════════════════════════════
async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)
    tier = ref_tier(user.referral_count)
    link = f"https://t.me/{BOT_USERNAME}?start={user.referral_code}"

    next_text = ""
    if tier["next"]:
        pct = min(100, int((user.referral_count / tier["target"]) * 100))
        next_text = f"\n📈 Next tier: *{tier['next']}* ({user.referral_count}/{tier['target']})\nProgress: [{p_bar(pct)}] {pct}%"

    await update.message.reply_text(
        f"👥 *Referral Program*\n\n🔗 Your link:\n`{link}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{tier['emoji']} Tier: *{tier['name']}*\n"
        f"👥 Referrals: *{user.referral_count}*\n"
        f"💰 Earnings: *{fmt(user.referral_earnings)} coins*\n"
        f"📊 Commission: *{DEFAULT_SETTINGS['referral_bonus_percent']}%* on mining\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n{next_text}\n\n"
        f"💡 Share your link to earn bonus coins!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Invite Friend", url=f"https://t.me/share/url?url={quote(link)}&text=Join%20{BOT_NAME}%20and%20earn%20crypto!")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")],
        ])
    )


# ═══════════════════════════════════════════════════════════════
# /daily
# ═══════════════════════════════════════════════════════════════
async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)
    now = datetime.now(timezone.utc)

    last_claim = user.last_daily_claim
    if last_claim:
        if isinstance(last_claim, str):
            last_claim = datetime.fromisoformat(last_claim)
        if last_claim.tzinfo is None:
            last_claim = last_claim.replace(tzinfo=timezone.utc)
        hours_since = (now - last_claim).total_seconds() / 3600
        if hours_since < 24:
            next_claim = last_claim + timedelta(hours=24)
            rem = next_claim - now
            return await update.message.reply_text(
                f"🎁 *Daily Bonus*\n\n⏰ Already claimed today!\nNext claim in: *{fmt_dur(rem.total_seconds())}*",
                parse_mode="Markdown"
            )
        if hours_since < 48:
            user.daily_streak += 1
        else:
            user.daily_streak = 1
    else:
        user.daily_streak = 1

    base = DEFAULT_SETTINGS["daily_claim_base"]
    streak_bonus = min(DEFAULT_SETTINGS["max_daily_streak_bonus"], base + user.daily_streak * 5)
    plan_mult = UPGRADE_PLANS.get(user.plan, {}).get("multiplier", 1)
    total = int(streak_bonus * plan_mult)

    user.balance += total
    user.total_mined += total
    user.daily_mined += total
    user.last_daily_claim = now
    await user.save()

    emojis = "🎉🎊✨💰🪙⭐"
    await update.message.reply_text(
        f"🎉 *Daily Bonus Claimed!*\n\n"
        f"🎰 Base: *{base} coins*\n"
        f"🔥 Streak Day *{user.daily_streak}* (+{user.daily_streak * 5})\n"
        f"📦 Plan: *{user.plan}* ({plan_mult}x)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *+{total} coins credited!*\n\n"
        f"💰 New balance: *{fmt(user.balance)}*\n⏰ Next claim in 24h",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⛏️ MINE", callback_data="cmd_mine"),
             InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]
        ])
    )


# ═══════════════════════════════════════════════════════════════
# /help
# ═══════════════════════════════════════════════════════════════
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    await update.message.reply_text(
        f"❓ *{BOT_NAME} Help*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⛏️ *Mining*\n  /mine — Start mining\n  /stopmine — Stop mining\n  /switch — Change mining coin\n  /boost — Buy boosts\n\n"
        f"💰 *Finance*\n  /balance — Check balance\n  /daily — Claim daily bonus\n  /withdraw — Withdraw\n  /upgrade — Upgrade plan\n\n"
        f"👥 *Social*\n  /referral — Referral system\n  /refleaderboard — Top referrers\n  /leaderboard — Top miners\n  /pool — Mining pools\n\n"
        f"📊 *Info*\n  /stats — Your statistics\n  /achievements — Achievements\n  /news — Crypto news\n  /halving — Halving countdown\n  /support — Get help\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📩 Contact @{ADMIN_USERNAME} for support",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


# ═══════════════════════════════════════════════════════════════
# /balance
# ═══════════════════════════════════════════════════════════════
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)
    hr = user.calculate_effective_hash_rate()
    daily_est = hr * DEFAULT_SETTINGS["mining_reward_max"] * 24 * 12

    await update.message.reply_text(
        f"💰 *Your Balance*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: *{fmt(user.balance)}*\n"
        f"📈 Total Mined: *{fmt(user.total_mined)}*\n"
        f"📅 Today: *{fmt(user.daily_mined)}*\n"
        f"📆 Week: *{fmt(user.weekly_mined)}*\n"
        f"🗓️ Month: *{fmt(user.monthly_mined)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Hash Rate: *{hr:.1f} H/s*\n"
        f"📊 Est. Daily: *~{fmt(daily_est)}*\n"
        f"🪙 Coin: *{user.mining_coin}*\n"
        f"📦 Plan: {p_icon(user.plan)} *{user.plan}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⛏️ MINE", callback_data="cmd_mine"),
             InlineKeyboardButton("💸 WITHDRAW", callback_data="cmd_withdraw")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")],
        ])
    )


# ═══════════════════════════════════════════════════════════════
# /stats, /achievements, /news, /leaderboard, /pool, /boost, /ping, /network
# ═══════════════════════════════════════════════════════════════
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)
    rank = await user.get_rank()
    hr = user.calculate_effective_hash_rate()
    ach = await user.get_achievements_progress()
    unlocked = sum(1 for a in ach if a["unlocked"])

    chart = ""
    for i in range(6, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%a")
        mined = user.daily_mined if i == 0 else random.random() * user.daily_mined * 0.8
        bar_len = min(15, int(mined / max(1, user.daily_mined) * 15))
        chart += f"{day} {'█' * bar_len} {fmt(mined)}\n"

    await _send_or_edit(update,
        f"📊 *Your Statistics*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Balances*\n  Current: *{fmt(user.balance)}*\n  Today: *{fmt(user.daily_mined)}*\n"
        f"  Week: *{fmt(user.weekly_mined)}*\n  Month: *{fmt(user.monthly_mined)}*\n"
        f"  All Time: *{fmt(user.total_mined)}*\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *Mining*\n  Hash Rate: *{hr:.1f} H/s*\n  Sessions: *{user.sessions}*\n"
        f"  Total Time: *{fmt_dur(user.total_mining_time)}*\n  Coin: *{user.mining_coin}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 *Rank: #{rank:,}* of *{TOTAL_USERS_DISPLAY:,}*\n"
        f"👥 Referrals: *{user.referral_count}*\n"
        f"🏆 Achievements: *{unlocked}/{len(ach)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 *Last 7 Days:*\n```\n{chart}```",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


async def achievements_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)
    prog = await user.get_achievements_progress()
    unlocked = [a for a in prog if a["unlocked"]]
    locked = [a for a in prog if not a["unlocked"]]

    text = f"🏆 *Achievements* ({len(unlocked)}/{len(prog)})\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    if unlocked:
        text += "✅ *Unlocked:*\n"
        for a in unlocked:
            text += f"  {a['icon']} *{a['name']}*\n"
        text += "\n"
    if locked:
        text += "🔒 *Locked:*\n"
        for a in locked[:8]:
            text += f"  {a['icon']} {a['name']} — [{p_bar(a['percent'])}] {a['percent']}%\n"

    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))


async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    shuffled = random.sample(NEWS_MESSAGES, min(3, len(NEWS_MESSAGES)))
    text = f"📰 *{BOT_NAME} News*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for n in shuffled:
        sentiment = "🟢" if n["sentiment"] == "bullish" else "🟡"
        text += f"{sentiment} *{n['title']}*\n_{n['body']}_\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    user = await cached_user(update.effective_user.id)
    top = await User.get_leaderboard(10)
    my_rank = await user.get_rank()

    text = "🏆 *Leaderboard — Top Miners*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"#{i+1}"
        name = u.get("username") or u.get("first_name") or f"User{u['user_id']}"
        text += f"{medal} *{name}* — {fmt(u['total_mined'])} {u.get('mining_coin', 'USDT')}\n    {p_icon(u.get('plan', 'Free'))} {u.get('plan', 'Free')}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n👈 *Your rank: #{my_rank:,}* of *{TOTAL_USERS_DISPLAY:,}*\n💰 Balance: *{fmt(user.balance)}*"

    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))


async def pool_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    user = await cached_user(update.effective_user.id)
    my_pool = await Pool.get_user_pool(user.user_id)
    all_pools = await Pool.get_active_pools()

    text = "🏊 *Mining Pools*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    if my_pool:
        text += f"✅ *Your Pool:* {my_pool.name}\n  👥 {len(my_pool.members)} members | ⚡ {fmt(my_pool.total_hash_rate)} H/s | 💸 {my_pool.fee}%\n\n"
    else:
        text += "❌ *Not in any pool*\n\n"
    text += "📋 *Available Pools:*\n"
    for p in all_pools:
        text += f"  {'💎' if p.get('type') == 'vip' else '🌐'} *{p['name']}* — {len(p.get('members', []))} members\n"

    buttons = []
    if not my_pool:
        buttons.append([InlineKeyboardButton("🌐 Join Public Pool", callback_data="pool_join")])
        if user.plan in ("Elite", "VIP"):
            buttons.append([InlineKeyboardButton("💎 Join VIP Pool", callback_data="pool_join_vip")])
    else:
        buttons.append([InlineKeyboardButton("🚪 Leave Pool", callback_data="pool_leave")])
    buttons.append([InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def boost_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    user = await cached_user(update.effective_user.id)
    now = datetime.now(timezone.utc)
    active = [b for b in user.boosts if b.get("active")]
    active_text = ""
    if active:
        active_text = "⏳ *Active Boosts:*\n"
        for b in active:
            exp = b.get("expires_at")
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp)
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            rem = (exp - now).total_seconds() if exp else 0
            active_text += f"  {b['type']} — *{fmt_dur(rem)} remaining*\n"
    else:
        active_text = "⏳ *No active boosts*\n"

    s = DEFAULT_SETTINGS
    await _send_or_edit(update,
        f"🚀 *Boost Store*\n\n{active_text}\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*2x Boost:*\n  1hr — *{s['boost2x_price']} coins*\n  6hr — *{s['boost2x_price']*4}* coins\n  24hr — *{s['boost2x_price']*12}* coins\n\n"
        f"*5x Boost:*\n  1hr — *{s['boost5x_price']} coins*\n  6hr — *{s['boost5x_price']*4}* coins\n  24hr — *{s['boost5x_price']*12}* coins\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n💡 Boosts stack! 2x + 5x = *10x* total!\n⚠️ Boosts require a *paid plan*.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ 2x 1hr", callback_data="boost_2x_1hr"),
             InlineKeyboardButton("⚡ 2x 6hr", callback_data="boost_2x_6hr"),
             InlineKeyboardButton("⚡ 2x 24hr", callback_data="boost_2x_24hr")],
            [InlineKeyboardButton("🔥 5x 1hr", callback_data="boost_5x_1hr"),
             InlineKeyboardButton("🔥 5x 6hr", callback_data="boost_5x_6hr"),
             InlineKeyboardButton("🔥 5x 24hr", callback_data="boost_5x_24hr")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")],
        ])
    )


async def network_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    user = await cached_user(update.effective_user.id)
    nh = f"{500 + random.random() * 300:.1f}"
    diff = f"{2000000 + random.randint(0, 500000):,}"
    bt = f"{10 + random.random() * 5:.1f}"
    mp = random.randint(0, 50)
    peers = random.randint(1000, 1500)

    await update.message.reply_text(
        f"🌐 *NETWORK STATUS*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Hashrate: *{nh} EH/s*\nDifficulty: *{diff}*\n"
        f"Block Time: *{bt}s* | Mempool: *{mp} tx*\nPeers: *{peers}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n⛏️ Your Hash: *{user.hash_rate} H/s*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


# ═══════════════════════════════════════════════════════════════
# /refleaderboard
# ═══════════════════════════════════════════════════════════════
async def refleaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    db = get_db()
    # Get top referrers sorted by referral_count
    cursor = db["users"].find(
        {"banned": False, "referral_count": {"$gt": 0}},
        {"_id": 0, "user_id": 1, "username": 1, "first_name": 1, "referral_count": 1, "referral_earnings": 1, "plan": 1}
    ).sort("referral_count", -1).limit(10)
    top = await cursor.to_list(10)

    user = await cached_user(user_id)
    my_rank_cursor = db["users"].count_documents(
        {"referral_count": {"$gt": user.referral_count}, "banned": False}
    )
    my_rank = my_rank_cursor + 1 if isinstance(my_rank_cursor, int) else 1

    text = "🏆 *Referral Leaderboard — Top Recruiters*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"#{i+1}"
        name = u.get("username") or u.get("first_name") or f"User{u['user_id']}"
        text += f"{medal} *{name}*\n  👥 {u['referral_count']} referrals | 💰 {fmt(u.get('referral_earnings', 0))} earned\n  {p_icon(u.get('plan', 'Free'))} {u.get('plan', 'Free')}\n\n"
    if not top:
        text += "_(No referrers yet)_\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━\n👈 *Your rank: #{my_rank:,}* | Referrals: *{user.referral_count}*"

    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


# ═══════════════════════════════════════════════════════════════
# /halving
# ═══════════════════════════════════════════════════════════════
async def halving_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    halving_date_str = DEFAULT_SETTINGS.get("halving_date")
    if not halving_date_str:
        # Default: ~90 days from now
        default_date = datetime.now(timezone.utc) + timedelta(days=90)
        DEFAULT_SETTINGS["halving_date"] = default_date.isoformat()
        halving_date_str = DEFAULT_SETTINGS["halving_date"]

    try:
        halving_date = datetime.fromisoformat(halving_date_str)
    except Exception:
        halving_date = datetime.now(timezone.utc) + timedelta(days=90)

    if halving_date.tzinfo is None:
        halving_date = halving_date.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    remaining = (halving_date - now).total_seconds()

    if remaining <= 0:
        text = (
            f"⏰ *NEXT HALVING*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n🔥 *Halving is happening NOW!*\n💰 Mining rewards are being halved.\n━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        days = int(remaining // 86400)
        hours = int((remaining % 86400) // 3600)
        mins = int((remaining % 3600) // 60)

        # Progress bar (assume 180 day cycle)
        total_cycle = 180 * 86400
        pct_elapsed = max(0, min(100, int((1 - remaining / total_cycle) * 100)))

        halving_date_display = halving_date.strftime("%B %d, %Y")
        text = f"⏰ *NEXT HALVING*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n📅 Date: *{halving_date_display}*\n⏳ Time left: *{days}d {hours}h {mins}m*\n📊 Progress: [{p_bar(pct_elapsed)}] {pct_elapsed}%\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n💡 *What is halving?*\nEvery ~180 days, mining rewards are\nreduced by 50%. Mine more now while\nrewards are still high!\n━━━━━━━━━━━━━━━━━━━━━━━━"

    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    await update.message.reply_text("🏓 *Pong!* Latency: Excellent", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))


async def myplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_state(user_id)
    user = await cached_user(user_id)
    plan = UPGRADE_PLANS[user.plan]
    idx = PLAN_ORDER.index(user.plan) if user.plan in PLAN_ORDER else 0
    next_p = UPGRADE_PLANS[PLAN_ORDER[idx+1]] if idx < len(PLAN_ORDER)-1 else None

    text = (
        f"📋 *MY PLAN*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{plan['icon']} *{plan['name']}*\n  ⚡ Hash: *{plan['hash_rate']} H/s* | *{plan['multiplier']}x*\n"
        f"  🏊 Pools: *{', '.join(plan['pool_access'])}*\n  🚀 Max Boosts: *{plan['max_boosts']}*\n"
        f"  ✅ {', '.join(plan['features'])}\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    buttons = []
    if next_p:
        text += f"\n⬆️ Next: {next_p['icon']} *{next_p['name']}* — *{next_p['price']} {next_p['coin']}*"
        buttons.append([InlineKeyboardButton("⬆️ UPGRADE", callback_data=f"upg_{PLAN_ORDER[idx+1]}")])
    buttons.append([InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    ticket = random.randint(1000, 9999)
    await update.message.reply_text(
        f"📩 *Support Center*\n\nNeed help? Here are your options:\n\n"
        f"1️⃣ Check the /help FAQ\n2️⃣ Report an issue\n3️⃣ Ask a question\n\n"
        f"Your message will be forwarded to our team.\nExpected response time: *24 hours*\n\n"
        f"Ticket ID: *#{ticket}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


async def earnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    user = await cached_user(update.effective_user.id)
    hr = user.calculate_effective_hash_rate()
    de = hr * DEFAULT_SETTINGS["mining_reward_max"] * 24 * 12
    await _send_or_edit(update,
        f"💰 *EARNINGS*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Hourly: *~{fmt(de/24)}*\n📅 Daily: *~{fmt(de)}*\n"
        f"📅 Weekly: *~{fmt(de*7)}*\n📅 Monthly: *~{fmt(de*30)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Actual: Today *{fmt(user.daily_mined)}* | All *{fmt(user.total_mined)}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


# ═══════════════════════════════════════════════════════════════
# ACTION HANDLERS (Inline Keyboard Callbacks)
# ═══════════════════════════════════════════════════════════════
async def action_cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    hr = user.calculate_effective_hash_rate()

    msg = (
        f"📰 *{BOT_NAME}*\n\n"
        f"🔥 Welcome back, *{(user.first_name or 'MINER').upper()}*! {p_icon(user.plan)}\n\n"
        f"📈 Hash Rate: *{hr:.1f}* H/s\n"
        f"💰 Balance: *{fmt(user.balance)}* {user.mining_coin}\n"
        f"📦 Plan: {p_icon(user.plan)} *{user.plan}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 *Quick Stats*\n  Today: *{fmt(user.daily_mined)}* | Week: *{fmt(user.weekly_mined)}*\n"
        f"  All Time: *{fmt(user.total_mined)}* | Referrals: *{user.referral_count}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if user.plan == "Free":
        msg += f"🚀 *BUY A PLAN* to boost mining!\n   🆓 Free → ⭐ Starter: 5 H/s | 💎 Pro: 25 H/s | 👑 Elite: 100 H/s\n"

    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=main_keyboard(query.from_user.id))


async def action_cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    s = get_session(user_id)
    if s["mining_interval"]:
        return await query.edit_message_text(
            "⛏️ *Already mining!* Use /stopmine first.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
        )
    if not await check_force_join(user_id, context):
        return

    user = await cached_user(user_id)
    hash_rate = user.calculate_effective_hash_rate()
    user.sessions += 1
    user._d["last_mine"] = datetime.now(timezone.utc)
    await user.save()
    sess = await MiningSession.create(user_id, user.username, hash_rate)

    md = {
        "start_time": datetime.now(timezone.utc), "blocks_found": 0, "total_mined": 0.0,
        "last_reward": 0.0, "last_event": None, "message_id": None,
        "session_id": sess.id, "coin": user.mining_coin, "hash_rate": hash_rate,
    }

    msg = await query.edit_message_text(
        _mining_msg(md, hash_rate, user.balance), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 ⏹️ STOP MINING", callback_data="stopmine")]])
    )
    md["message_id"] = query.message.message_id
    md["chat_id"] = query.message.chat.id
    s["mining_data"] = md

    interval = DEFAULT_SETTINGS["mining_interval_seconds"]

    async def mining_tick():
        try:
            u = await User.find_one(user_id)
            if not u or u.banned:
                return
            reward = calc_reward(md["hash_rate"], md["coin"])
            roll = random.random()
            event_text = None
            if roll < DEFAULT_SETTINGS["mega_bonus_chance"]:
                reward *= 10; event_text = "🌟 MEGA BONUS! 10x!"
            elif roll < DEFAULT_SETTINGS["super_bonus_chance"] + DEFAULT_SETTINGS["mega_bonus_chance"]:
                reward *= 5; event_text = "🦄 SUPER BONUS! 5x!"
            elif roll < DEFAULT_SETTINGS["bonus_block_chance"] + DEFAULT_SETTINGS["super_bonus_chance"] + DEFAULT_SETTINGS["mega_bonus_chance"]:
                reward *= 2; event_text = "🎉 BONUS BLOCK! 2x!"
            u.add_mining_reward(reward)
            u.calculate_effective_hash_rate()
            await u.save()
            md["blocks_found"] += 1; md["total_mined"] += reward
            md["last_reward"] = reward; md["last_event"] = event_text
        except Exception as e:
            logger.error(f"Mining tick error: {e}")

    s["mining_interval"] = context.job_queue.run_repeating(mining_tick, interval=interval, first=interval)

    async def card_tick():
        await _mining_card_tick(context, user_id, s)

    s["mining_card"] = context.job_queue.run_repeating(card_tick, interval=2, first=2)


async def action_stopmine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    s = get_session(user_id)
    if not s["mining_interval"]:
        return await query.edit_message_text("⛏️ You are not mining.")
    job = s["mining_interval"]
    job.schedule_removal()
    s["mining_interval"] = None
    card = s.get("mining_card")
    if card:
        try:
            card.schedule_removal()
        except Exception:
            pass
    s["mining_card"] = None
    md = s["mining_data"]
    if not md:
        return await query.edit_message_text("Mining stopped.")
    dur = int((datetime.now(timezone.utc) - md["start_time"]).total_seconds())
    s["mining_data"] = None
    await query.edit_message_text(
        f"⛏️ *Mining Session Ended*\n\n⏱️ Duration: *{fmt_dur(dur)}*\n🏆 Blocks: *{md['blocks_found']}*\n"
        f"💰 Mined: *{fmt(md['total_mined'])} {md['coin']}*\n⚡ Hash: *{md['hash_rate']} H/s*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⛏️ MINE AGAIN", callback_data="cmd_mine"),
             InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]
        ])
    )


async def action_cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    hr = user.calculate_effective_hash_rate()
    await query.edit_message_text(
        f"💰 *Your Balance*\n\n💰 Balance: *{fmt(user.balance)}*\n⚡ Hash: *{hr:.1f} H/s*\n📦 Plan: {p_icon(user.plan)} *{user.plan}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⛏️ Mine", callback_data="cmd_mine"),
             InlineKeyboardButton("⬆️ Upgrade", callback_data="cmd_upgrade")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")],
        ])
    )


async def action_cmd_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    if user.plan == "Free":
        return await query.edit_message_text(
            "🚫 *Withdrawal Restricted*\n\n❌ You need a *paid plan* to withdraw.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬆️ Upgrade", callback_data="cmd_upgrade")],
                [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]
            ])
        )
    if user.balance < DEFAULT_SETTINGS["min_withdrawal"]:
        return await query.edit_message_text(
            f"💸 *Withdrawal*\n\n❌ Insufficient balance.\nMinimum: *{DEFAULT_SETTINGS['min_withdrawal']}*\nBalance: *{fmt(user.balance)}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
        )
    s = get_session(query.from_user.id)
    s["state"] = "awaiting_withdraw_amount"
    s["temp_data"] = {}
    await query.edit_message_text(
        f"💸 *Withdrawal*\n\nBalance: *{fmt(user.balance)} coins*\nMinimum: *{DEFAULT_SETTINGS['min_withdrawal']} coins*\n\n💰 Enter amount to withdraw:",
        parse_mode="Markdown"
    )


async def action_cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    now = datetime.now(timezone.utc)
    last = user.last_daily_claim
    if last:
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() / 3600 < 24:
            rem = (last + timedelta(hours=24) - now).total_seconds() / 60
            return await query.edit_message_text(f"🎁 *Daily Bonus*\n\n⏰ Next claim in *{rem:.0f} min*", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))

    base = DEFAULT_SETTINGS["daily_claim_base"]
    streak = (user.daily_streak or 0) + 1
    plan_mult = UPGRADE_PLANS.get(user.plan, {}).get("multiplier", 1)
    total = int((base + streak * 5) * plan_mult)
    user.balance += total; user.total_mined += total; user.daily_mined += total
    user.last_daily_claim = now; user.daily_streak = streak
    await user.save()
    await query.edit_message_text(
        f"🎉 *Daily Claimed!*\n\n💰 *+{total} coins*\n🔥 Streak: Day *{streak}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


async def action_cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    text = f"⬆️ *Upgrade Plan*\n\nCurrent: {p_icon(user.plan)} *{user.plan}*\n\n"
    for name in PLAN_ORDER[1:]:
        p = UPGRADE_PLANS[name]
        text += f"{p['icon']} *{p['name']}* — {p['price']} {p['coin']} | {p['hash_rate']} H/s\n"
    await query.edit_message_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬆️ View Plans", callback_data="cmd_upgrade_full")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]
        ]))


async def action_cmd_upgrade_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    text = "⬆️ *Compare Plans*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for name in PLAN_ORDER:
        p = UPGRADE_PLANS[name]
        is_current = user.plan == name
        text += f"\n{p['icon']} *{p['name']}*{' 👈' if is_current else ''}\n  💰 *{p['price']} {p['coin']}* | ⚡ *{p['hash_rate']} H/s*\n  📈 *{p['multiplier']}x* multiplier\n  ✅ {', '.join(p['features'])}\n"
    await query.edit_message_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))


async def action_cmd_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    tier = ref_tier(user.referral_count)
    link = f"https://t.me/{BOT_USERNAME}?start={user.referral_code}"
    await query.edit_message_text(
        f"👥 *Referral Program*\n\n🔗 `{link}`\n\n{tier['emoji']} Tier: *{tier['name']}*\n👥 Referrals: *{user.referral_count}*\n💰 Earnings: *{fmt(user.referral_earnings)}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={quote(link)}&text=Join%20{BOT_NAME}!")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")],
        ])
    )


async def action_cmd_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    prog = await user.get_achievements_progress()
    unlocked = [a for a in prog if a["unlocked"]]
    locked = [a for a in prog if not a["unlocked"]]
    text = f"🏆 *Achievements* ({len(unlocked)}/{len(prog)})\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    if unlocked:
        text += "✅ *Unlocked:*\n"
        for a in unlocked[:8]:
            text += f"  {a['icon']} *{a['name']}*\n"
    if locked:
        text += "\n🔒 *Locked:*\n"
        for a in locked[:5]:
            text += f"  {a['icon']} {a['name']} — [{p_bar(a['percent'])}] {a['percent']}%\n"
    await query.edit_message_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]]))


async def action_cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"❓ *{BOT_NAME} Help*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⛏️ /mine — Start mining\n⏹️ /stopmine — Stop\n🪙 /switch — Change coin\n🚀 /boost — Boosts\n"
        f"💰 /balance — Balance\n🎁 /daily — Daily bonus\n💸 /withdraw — Withdraw\n⬆️ /upgrade — Upgrade\n"
        f"👥 /referral — Referrals\n🏆 /refleaderboard — Top referrers\n🏆 /leaderboard — Top miners\n🏊 /pool — Pools\n"
        f"📊 /stats — Statistics\n🏆 /achievements — Achievements\n📰 /news — News\n⏰ /halving — Halving countdown\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n📩 Contact @{ADMIN_USERNAME} for support",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


async def action_cmd_myplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    plan = UPGRADE_PLANS[user.plan]
    idx = PLAN_ORDER.index(user.plan) if user.plan in PLAN_ORDER else 0
    next_p = UPGRADE_PLANS[PLAN_ORDER[idx+1]] if idx < len(PLAN_ORDER)-1 else None
    text = f"📋 *MY PLAN*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n{plan['icon']} *{plan['name']}*\n  ⚡ *{plan['hash_rate']} H/s* | *{plan['multiplier']}x*\n  ✅ {', '.join(plan['features'])}\n"
    buttons = []
    if next_p:
        text += f"\n⬆️ Next: {next_p['icon']} *{next_p['name']}* — *{next_p['price']} {next_p['coin']}*"
        buttons.append([InlineKeyboardButton("⬆️ UPGRADE", callback_data=f"upg_{PLAN_ORDER[idx+1]}")])
    buttons.append([InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def action_cmd_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await cached_user(query.from_user.id)
    await query.edit_message_text(
        f"🌐 *NETWORK STATUS*\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Hashrate: *{500+random.random()*300:.1f} EH/s*\n"
        f"Difficulty: *{2000000+random.randint(0,500000):,}*\n"
        f"Block Time: *{10+random.random()*5:.1f}s*\nPeers: *{random.randint(1000,1500)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n⛏️ Your Hash: *{user.hash_rate} H/s*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


async def action_cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await stats_cmd(update, context)


async def action_cmd_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await boost_cmd(update, context)


async def action_cmd_earnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await earnings_cmd(update, context)


async def action_cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    n = random.choice(NEWS_MESSAGES)
    sentiment = "🟢" if n["sentiment"] == "bullish" else "🟡"
    await query.edit_message_text(
        f"📰 *{n['title']}*\n\n{n['body']}\n\n_{sentiment} {n['sentiment'].upper()}_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Another", callback_data="cmd_news")],
            [InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]
        ])
    )


# ── Upgrade Plan Selection ──
async def action_upg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_name = query.data.replace("upg_", "")
    if plan_name not in UPGRADE_PLANS:
        return
    user = await cached_user(query.from_user.id)
    plan = UPGRADE_PLANS[plan_name]
    if user.plan == plan_name:
        return await query.edit_message_text(f"✅ You are already on *{plan_name}*!", parse_mode="Markdown")

    usd_price = float(plan["price"])
    # Build crypto selection buttons
    buttons = []
    row = []
    for c in SUPPORTED_CRYPTOS:
        info = COIN_INFO.get(c, {})
        coin_price = market_price(c)
        crypto_amt = round(usd_price / coin_price, 8) if coin_price > 0 else 0
        row.append(InlineKeyboardButton(
            f"{info.get('emoji', '🪙')} {c} ({crypto_amt})",
            callback_data=f"pay_select_{plan_name}_{c}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_upgrade")])

    await query.edit_message_text(
        f"⬆️ *Upgrade to {plan_name}*\n\n{plan['icon']} *{plan['name']}*\n⚡ Hash: *{plan['hash_rate']} H/s*\n📈 Mult: *{plan['multiplier']}x*\n💰 Price: *${usd_price}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n✅ *Perks:*\n" + "\n".join(f"  • {f}" for f in plan['features']) +
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n🪙 *Choose payment method:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def action_pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_name = query.data.replace("pay_crypto_", "")
    user = await cached_user(query.from_user.id)
    plan = UPGRADE_PLANS.get(plan_name, {})
    coin = plan.get("coin", "USDT")
    usd_price = float(plan.get("price", 0) or 0)
    coin_price = market_price(coin)
    crypto_amount = round(usd_price / coin_price, 8) if coin_price > 0 else 0
    addr = get_wallet("USDT_TRC20") if coin == "USDT" else get_wallet(coin)

    pr = await PaymentRequest.create(user.user_id, plan_name, plan["price"], coin, addr, user.username)
    user.pending_payment_id = str(pr.id)
    await user.save()

    await query.edit_message_text(
        f"💳 *Crypto Payment*\n\nPlan: {plan.get('icon', '📦')} *{plan_name}*\nAmount: *{crypto_amount} {coin}* (≈ ${usd_price})\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n📤 *Send exactly* `{crypto_amount} {coin}` *to:*\n`{addr}`\n\n⚠️ Send the EXACT amount. Payment expires in 24h.\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n📸 *After sending, click I've Paid and send a screenshot.*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 ✅ I'VE PAID", callback_data=f"pay_paid_{pr.id}")],
            [InlineKeyboardButton("🔴 ❌ CANCEL", callback_data="pay_cancel")],
        ])
    )


async def action_pay_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = query.data.replace("pay_paid_", "")
    user = await cached_user(query.from_user.id)
    user.waiting_for_screenshot = True
    user.pending_payment_id = pid
    await user.save()
    await query.edit_message_text(
        "📸 *Send Screenshot*\n\nPlease send a screenshot of your transaction.\n\nInclude: TX hash, amount, recipient.\n⏳ Verification within 15 min.",
        parse_mode="Markdown"
    )


async def action_pay_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Cancelled")
    user = await cached_user(query.from_user.id)
    user.pending_payment_id = None
    user.waiting_for_screenshot = False
    await user.save()
    await query.edit_message_text("❌ Payment cancelled. Use /upgrade to try again.")


# ── Boost Purchase ──
async def action_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.replace("boost_", "")
    parts = data.split("_")
    if len(parts) != 2:
        return
    btype, duration = parts
    s = DEFAULT_SETTINGS
    price_map = {
        "2x_1hr": s["boost2x_price"], "2x_6hr": s["boost2x_price"]*4, "2x_24hr": s["boost2x_price"]*12,
        "5x_1hr": s["boost5x_price"], "5x_6hr": s["boost5x_price"]*4, "5x_24hr": s["boost5x_price"]*12,
    }
    price = price_map.get(data, 0)
    user = await cached_user(query.from_user.id)
    if user.balance < price:
        return await query.edit_message_text(f"❌ Insufficient balance!\nNeed: *{price}* | Have: *{fmt(user.balance)}*", parse_mode="Markdown")
    user.balance -= price
    exp = datetime.now(timezone.utc) + timedelta(seconds=s["boost_durations"].get(duration, 3600))
    boosts = user._d.get("boosts", [])
    boosts.append({"type": btype, "duration": duration, "expires_at": exp, "active": True, "activated_at": datetime.now(timezone.utc)})
    user._d["boosts"] = boosts
    user.calculate_effective_hash_rate()
    await user.save()
    await query.edit_message_text(
        f"🚀 *Boost Activated!*\n\n{'⚡' if btype == '2x' else '🔥'} *{btype} Boost*\n"
        f"⏱️ Duration: *{duration.replace('hr', ' hours')}*\n"
        f"⚡ New Hash Rate: *{user.hash_rate:.1f} H/s*\n💰 Balance: *{fmt(user.balance)}*",
        parse_mode="Markdown"
    )


# ── Coin Switch ──
async def action_coin_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    coin = query.data.replace("sw_", "")
    if coin not in SUPPORTED_CRYPTOS:
        return await query.answer("Invalid coin")
    user = await cached_user(query.from_user.id)
    user.mining_coin = coin
    await user.save()
    await query.answer(f"Switched to {coin}")
    await query.edit_message_text(
        f"🪙 *Switched to {c_emoji(coin)} {coin}!*\n\n💰 Your mining rewards will now be in {coin}.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
    )


# ── Force Join Check ──
async def action_force_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    # Record the tap first: this unlocks invite-only (link-only) groups that
    # Telegram won't let the bot verify. Real channels with an id/@username
    # are still checked server-side and cannot be bypassed this way.
    await _fj_mark_confirmed(user_id)
    if await check_force_join(user_id, context):
        await query.answer("✅ All good!")
        await query.edit_message_text("✅ Access granted! Use /start to begin.", reply_markup=None)
    else:
        await query.answer("❌ You haven't joined all channels yet!")


# ── Pool actions ──
async def action_pool_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pool_type = "vip" if "vip" in query.data else "public"
    user = await cached_user(query.from_user.id)
    if pool_type == "vip" and user.plan not in ("Elite", "VIP"):
        return await query.answer("VIP pool requires Elite or VIP plan!")
    pool = await Pool.get_vip_pool() if pool_type == "vip" else await Pool.get_public_pool()
    if user.user_id not in pool.members:
        pool._d.setdefault("members", []).append(user.user_id)
        pool.total_hash_rate += user.hash_rate
        await pool.save()
    await query.answer("✅ Joined pool!")
    await pool_cmd(Update(message=query.message, effective_user=query.from_user, update_id=0), context)


async def action_pool_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = await cached_user(query.from_user.id)
    pool = await Pool.get_user_pool(user.user_id)
    if pool:
        pool._d["members"] = [m for m in pool.members if m != user.user_id]
        pool.total_hash_rate = max(0, pool.total_hash_rate - user.hash_rate)
        await pool.save()
    await query.answer("Left pool")
    await pool_cmd(Update(message=query.message, effective_user=query.from_user, update_id=0), context)


async def _try_auto_verify(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    """User sent a TX hash after their payment screenshot → verify on-chain.

    Genuine receipts auto-verify the plan. Anything doubtful (fake/edited
    screenshot, wrong amount, unknown tx) is flagged for manual admin review.
    """
    from bson import ObjectId
    from models.payment_request import PaymentRequest, grant_plan
    from config.chain_verify import verify_transaction

    txt = (update.message.text or "").strip()
    if txt.lower() in ("skip", "manual", "later", "no"):
        user.waiting_for_tx_hash = False
        await user.save()
        return await update.message.reply_text(
            "👌 No problem — your screenshot goes to manual review by our team. "
            "You'll be notified once it's verified.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
        )

    pid = user.pending_payment_id
    pr = None
    if pid:
        try:
            pr = await PaymentRequest.find_by_id(ObjectId(pid))
        except Exception:
            pr = None
    user.waiting_for_tx_hash = False
    await user.save()
    if not pr:
        return await update.message.reply_text(
            "❌ I couldn't find your pending payment. Your screenshot is with our team — "
            "manual review it is.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
        )

    await update.message.reply_text(
        f"🔍 Checking transaction `{txt[:24]}{'…' if len(txt) > 24 else ''}` on the blockchain… (few seconds)",
        parse_mode="Markdown",
    )
    res = await verify_transaction(pr.coin, pr.address, txt, float(pr.amount or 0))
    status = res.get("status")
    uname = f"@{user.username}" if user.username else str(user.user_id)
    coin_p = pr.coin or "?"

    if status == "ok":
        pr.status = "verified"
        pr.verified_at = datetime.now(timezone.utc)
        pr.tx_hash = txt
        pr._d["auto_verified"] = True
        pr._d["chain_detail"] = res.get("detail", "")
        await pr.save()
        u2, plan = await grant_plan(user.user_id, pr.plan)
        invalidate_user_cache(user.user_id)
        await update.message.reply_text(
            f"🎉 *Payment Verified Automatically!*\n\n"
            f"✅ We confirmed your transaction on the blockchain.\n"
            f"{plan.get('icon', '📦')} Your plan is now *{pr.plan}*!\n⚡ Hash Rate: *{plan.get('hash_rate', '?')} H/s*\n"
            f"\n_This was checked against the real blockchain — no waiting for manual review._",
            parse_mode="Markdown",
        )
        for sid in all_staff_ids():
            try:
                await context.bot.send_message(
                    sid,
                    f"🟢 *AUTO-VERIFIED* (on-chain)\n\n👤 {uname} (`{user.user_id}`)\n📦 Plan: *{pr.plan}* | 💰 {pr.amount} {coin_p}\n🔗 `{txt[:32]}…`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return

    # Not verified — flag for the humans, never auto-approve
    if status in ("not_found", "amount_mismatch", "wrong_recipient", "unconfirmed"):
        pr._d["chain_result"] = status
        pr._d["chain_detail"] = res.get("detail", "")
        await pr.save()
        await update.message.reply_text(
            f"⚠️ *We could not confirm this payment on the blockchain.*\n\n{res.get('detail', '')}\n\n"
            f"Your screenshot has been sent to manual review by our team — they may contact you. "
            f"If you believe this is a mistake, reply with the correct TX ID or contact support.",
            parse_mode="Markdown",
        )
        for sid in all_staff_ids():
            try:
                await context.bot.send_message(
                    sid,
                    f"⚠️ *CHAIN CHECK FAILED — possible fake receipt*\n\n👤 {uname} (`{user.user_id}`)\n📦 Plan: *{pr.plan}* | 💰 {pr.amount} {coin_p}\n🧾 Check: *{status}*\n📝 {res.get('detail', '')}\n\nReview the screenshot they sent and Verify/Reject manually.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return

    # network_error / unsupported / invalid_hash → manual review, keep it friendly
    frag = {'invalid_hash': 'could not read that TX ID — double-check it',
            'network_error': 'is temporarily unavailable',
            'unsupported': 'isn’t available for this coin'}.get(status, 'did not complete')
    await update.message.reply_text(
        f"ℹ️ Automatic verification {frag}.\n"
        f"Your screenshot is with our team for manual review — you'll be notified once verified.",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════════════════════════════════
# TEXT MESSAGE HANDLER (for withdrawal flow & screenshot)
# ═══════════════════════════════════════════════════════════════
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    s = get_session(user_id)

    # Handle screenshot for payment verification
    user = await cached_user(user_id)
    if user.waiting_for_screenshot and update.message.photo:
        pr = None
        if user.pending_payment_id:
            from bson import ObjectId
            try:
                pr = await PaymentRequest.find_by_id(ObjectId(user.pending_payment_id))
                if pr:
                    pr.screenshot_file_id = update.message.photo[-1].file_id
                    pr.status = "screenshot_received"
                    await pr.save()
            except Exception:
                pass
        user.waiting_for_screenshot = False
        await user.save()

        photo = update.message.photo[-1].file_id
        detail = (
            f"💳 *New Payment Screenshot*\n\n"
            f"👤 User: `{user_id}` ({('@' + user.username) if user.username else 'no username'})\n"
            f"📦 Plan: *{pr.plan if pr else '?'}*\n"
            f"💰 Amount: *{pr.amount if pr else '?'} {pr.coin if pr else '?'}*\n"
            f"🔎 Status: *screenshot_received*"
        )
        buttons = None
        if pr:
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Verify", callback_data=f"adm_pay_verify_{pr.id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"adm_pay_reject_{pr.id}")]
            ])
        # Forward the actual photo to EVERY staff member ONLY (private — never to groups)
        for admin_id in all_staff_ids():
            try:
                await context.bot.send_photo(
                    admin_id, photo, caption=detail,
                    parse_mode="Markdown", reply_markup=buttons,
                )
            except Exception:
                pass

        # Ask for the TX hash so the bot can auto-verify the receipt on-chain
        if pr and DEFAULT_SETTINGS.get("chain_verify_enabled", True):
            user.waiting_for_tx_hash = True
            await user.save()
            return await update.message.reply_text(
                "📸 *Screenshot received!*\n\n🔗 Now send the *transaction hash (TX ID)* "
                "from your wallet so we can verify it automatically on the blockchain "
                "(paste the long code, or send `skip` for manual review).",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
            )
        return await update.message.reply_text(
            "📸 Screenshot received! Our team will verify your payment within 15 minutes.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
        )

    # ── Auto chain-verify: user pastes their TX hash after the screenshot ──
    if user.waiting_for_tx_hash and update.message.text:
        return await _try_auto_verify(update, context, user)

    # Handle withdrawal flow
    if s.get("state") == "awaiting_withdraw_amount":
        try:
            amount = float(update.message.text.strip())
        except ValueError:
            return await update.message.reply_text("❌ Please enter a valid number.")

        if amount < DEFAULT_SETTINGS["min_withdrawal"]:
            return await update.message.reply_text(
                f"❌ Minimum withdrawal is *{DEFAULT_SETTINGS['min_withdrawal']} coins*.",
                parse_mode="Markdown"
            )
        if amount > user.balance:
            return await update.message.reply_text(
                f"❌ Insufficient balance. You have *{fmt(user.balance)} coins*.",
                parse_mode="Markdown"
            )
        if amount > DEFAULT_SETTINGS["max_withdrawal_daily"]:
            return await update.message.reply_text(
                f"❌ Maximum daily withdrawal is *{DEFAULT_SETTINGS['max_withdrawal_daily']} coins*.",
                parse_mode="Markdown"
            )

        s["temp_data"]["amount"] = amount
        s["state"] = "awaiting_withdraw_coin"
        # Show crypto selection buttons
        buttons = []
        row = []
        for c in SUPPORTED_CRYPTOS:
            info = COIN_INFO.get(c, {})
            row.append(InlineKeyboardButton(f"{info.get('emoji', '🪙')} {c}", callback_data=f"wd_coin_{c}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cmd_back")])
        return await update.message.reply_text(
            f"💸 Withdrawal: *{fmt(amount)} coins*\n\n🪙 Choose payment method to receive:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    if s.get("state") == "awaiting_withdraw_address":
        address = update.message.text.strip()
        amount = s["temp_data"].get("amount", 0)
        coin = s["temp_data"].get("withdraw_coin", user.mining_coin)

        # Create withdrawal WITHOUT deducting balance yet — admin must approve first
        w = await Withdrawal.create(
            user_id, user.username, amount, address, coin, user.plan
        )

        s["state"] = None
        s["temp_data"] = None

        wd_msg = f"✅ *Withdrawal Request Submitted!*\n\n💰 Amount: *{fmt(amount)} {coin}*\n📍 Address: `{address}`\n📋 Status: *Pending Approval*\n\n⏳ Your request has been sent to our team for review.\n💰 You will be notified once it is processed.\n\n💰 Current balance: *{fmt(user.balance)}*"
        await update.message.reply_text(
            wd_msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ MENU", callback_data="cmd_back")]])
        )

        # Notify ALL admins with approve/reject buttons
        wd_id = str(w.id)
        admin_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"adm_wd_approve_{wd_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"adm_wd_reject_{wd_id}")
            ]
        ])
        for admin_id in all_staff_ids():
            try:
                username_display = f"@{user.username}" if user.username else str(user_id)
                await context.bot.send_message(
                    admin_id,
                    f"💸 *New Withdrawal Request*\n\n👤 User: {username_display} (ID: `{user_id}`)\n📦 Plan: {p_icon(user.plan)} *{user.plan}*\n💰 Amount: *{fmt(amount)} {coin}*\n📍 Address: `{address}`\n💰 User Balance: *{fmt(user.balance)}*\n📋 Status: *Pending*",
                    parse_mode="Markdown",
                    reply_markup=admin_kb,
                )
            except Exception:
                pass

        return

    # Default: any other message gets a hint so the bot never looks dead
    if (update.message.text and not update.message.text.startswith("/")) or update.message.photo:
        return await update.message.reply_text(
            "👋 I didn't understand that.\n\n"
            "Try /start to open the menu, or /help for all commands.",
            parse_mode="Markdown",
        )


# ═══════════════════════════════════════════════════════════════
# WITHDRAWAL CRYPTO SELECTION
# ═══════════════════════════════════════════════════════════════
async def action_wd_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    s = get_session(user_id)
    coin = query.data.replace("wd_coin_", "")
    if coin not in SUPPORTED_CRYPTOS:
        return await query.answer("Invalid coin")
    s["temp_data"]["withdraw_coin"] = coin
    s["state"] = "awaiting_withdraw_address"
    info = COIN_INFO.get(coin, {})
    await query.edit_message_text(
        f"💸 Withdrawal: *{fmt(s['temp_data']['amount'])} coins* → *{info.get('emoji', '🪙')} {coin}*\n\n"
        f"📍 Enter your *{coin}* withdrawal address:",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════
# PAYMENT CRYPTO SELECTION (for upgrades)
# ═══════════════════════════════════════════════════════════════
async def action_pay_crypto_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.replace("pay_select_", "")
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    plan_name, coin = parts
    if coin not in SUPPORTED_CRYPTOS:
        return await query.answer("Invalid coin")
    plan = UPGRADE_PLANS.get(plan_name, {})
    if not plan:
        return
    user = await cached_user(query.from_user.id)
    # Calculate price in selected coin
    usd_price = float(plan["price"])
    coin_price = market_price(coin)
    crypto_amount = round(usd_price / coin_price, 8) if coin_price > 0 else 0
    addr = get_wallet(coin)

    pr = await PaymentRequest.create(user.user_id, plan_name, str(crypto_amount), coin, addr, user.username)
    user.pending_payment_id = str(pr.id)
    await user.save()

    await query.edit_message_text(
        f"💳 *Crypto Payment*\n\nPlan: {plan.get('icon', '📦')} *{plan_name}*\nAmount: *{crypto_amount} {coin}* (≈ ${usd_price})\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n📤 *Send exactly* `{crypto_amount} {coin}` *to:*\n`{addr}`\n\n⚠️ Send the EXACT amount. Payment expires in 24h.\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n📸 *After sending, click I've Paid and send a screenshot.*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 ✅ I'VE PAID", callback_data=f"pay_paid_{pr.id}")],
            [InlineKeyboardButton("🔴 ❌ CANCEL", callback_data="pay_cancel")],
        ])
    )


# ═══════════════════════════════════════════════════════════════
# PAINTED BUTTONS — real button colors (Telegram Bot API 9.4)
# ═══════════════════════════════════════════════════════════════
# Bot API 9.4+ lets bots paint inline buttons via the `style` field:
#   "danger" (red) | "success" (green) | "primary" (blue)
# The pinned python-telegram-bot (21.6) predates the field, so we inject
# it at serialization time — the same approach styled panels use. The
# style mapping lives in config.constants.paint_button_label, shared
# with the admin panel for building color summaries.
_ORIGINAL_IKB_TO_DICT = InlineKeyboardButton.to_dict


def _style_override(cb):
    """Per-button color pinned in the admin panel (🖌️). Keys are callback
    values, exact or prefix patterns ending in '*'. Returns a style or None.
    Pinned colors apply even when the global 'Colored Buttons' toggle is off."""
    if not cb:
        return None
    ov = DEFAULT_SETTINGS.get("button_color_overrides") or {}
    if cb in ov:
        return ov[cb] or None
    for k, v in ov.items():
        if k.endswith("*") and cb.startswith(k[:-1]):
            return v or None
    return None


def _styled_ikb_to_dict(self, recursive=True):
    data = _ORIGINAL_IKB_TO_DICT(self, recursive)
    text = data.get("text", "")
    auto_style, clean_label = paint_button_label(text)
    cb = data.get("callback_data")
    final_style = _style_override(cb if isinstance(cb, str) else None)
    if final_style is None and DEFAULT_SETTINGS.get("button_colors", True):
        final_style = auto_style
    if final_style:
        data["style"] = final_style
        if clean_label != text:
            data["text"] = clean_label
    return data


def _patch_button_colors():
    """Make every inline button serialize with its painted `style`.
    Idempotent — safe to call more than once."""
    if getattr(InlineKeyboardButton, "to_dict", None) is not _styled_ikb_to_dict:
        InlineKeyboardButton.to_dict = _styled_ikb_to_dict


_patch_button_colors()


# ═══════════════════════════════════════════════════════════════
# STYLED BOT — applies the small-caps "cool font" UI automatically
# ═══════════════════════════════════════════════════════════════
def _style_for(text, chat_id):
    """Apply the global UI style (small caps / title case / plain) to a message.

    Style comes from the admin panel (🎨 UI STYLE). Staff chats keep the plain
    font unless that staff member enabled their personal '👤 MY PANEL FONT'.
    """
    if not isinstance(text, str) or not text:
        return text
    style = DEFAULT_SETTINGS.get("ui_style", "smallcaps")
    if style == "plain":
        return text
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        cid = None
    if cid is not None and cid > 0:
        if cid in all_staff_ids() and cid not in (DEFAULT_SETTINGS.get("styled_admins") or []):
            return text
    if style == "title":
        return title_text(text)
    return styled(text)


class StyledExtBot(ExtBot):
    """ExtBot that automatically renders every outgoing message in the
    small-caps UI font, keeping code/link/@handle parts intact."""

    async def send_message(self, chat_id, text, **kwargs):
        return await super().send_message(chat_id, _style_for(text, chat_id), **kwargs)

    async def edit_message_text(self, text, chat_id=None, message_id=None,
                                inline_message_id=None, **kwargs):
        return await super().edit_message_text(
            _style_for(text, chat_id), chat_id=chat_id, message_id=message_id,
            inline_message_id=inline_message_id, **kwargs,
        )

    async def send_photo(self, chat_id, photo, **kwargs):
        if kwargs.get("caption") is not None:
            kwargs["caption"] = _style_for(kwargs["caption"], chat_id)
        return await super().send_photo(chat_id, photo, **kwargs)


# ═══════════════════════════════════════════════════════════════
# BUILD BOT
# ═══════════════════════════════════════════════════════════════
def build_bot():
    # ForceJoinApplication gates every update behind the channel-join check;
    # StyledExtBot gives the UI its small-caps font automatically.
    app = (
        Application.builder()
        .bot(StyledExtBot(token=BOT_TOKEN))
        .application_class(ForceJoinApplication)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("mine", mine_cmd))
    app.add_handler(CommandHandler("stopmine", stopmine_cmd))
    app.add_handler(CommandHandler("withdraw", withdraw_cmd))
    app.add_handler(CommandHandler("switch", switch_cmd))
    app.add_handler(CommandHandler("upgrade", upgrade_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("daily", daily_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("achievements", achievements_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("pool", pool_cmd))
    app.add_handler(CommandHandler("boost", boost_cmd))
    app.add_handler(CommandHandler("network", network_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("myplan", myplan_cmd))
    app.add_handler(CommandHandler("support", support_cmd))
    app.add_handler(CommandHandler("earnings", earnings_cmd))
    app.add_handler(CommandHandler("refleaderboard", refleaderboard_cmd))
    app.add_handler(CommandHandler("halving", halving_cmd))

    # Callback queries
    app.add_handler(CallbackQueryHandler(action_cmd_back, pattern="^cmd_back$"))
    app.add_handler(CallbackQueryHandler(action_cmd_mine, pattern="^cmd_mine$"))
    app.add_handler(CallbackQueryHandler(action_stopmine, pattern="^stopmine$"))
    app.add_handler(CallbackQueryHandler(action_cmd_balance, pattern="^cmd_balance$"))
    app.add_handler(CallbackQueryHandler(action_cmd_upgrade, pattern="^cmd_upgrade$"))
    app.add_handler(CallbackQueryHandler(action_cmd_upgrade_full, pattern="^cmd_upgrade_full$"))
    app.add_handler(CallbackQueryHandler(action_cmd_withdraw, pattern="^cmd_withdraw$"))
    app.add_handler(CallbackQueryHandler(action_cmd_referral, pattern="^cmd_referral$"))
    app.add_handler(CallbackQueryHandler(action_cmd_daily, pattern="^cmd_daily$"))
    app.add_handler(CallbackQueryHandler(action_cmd_achievements, pattern="^cmd_achievements$"))
    app.add_handler(CallbackQueryHandler(action_cmd_help, pattern="^cmd_help$"))
    app.add_handler(CallbackQueryHandler(action_cmd_myplan, pattern="^cmd_myplan$"))
    app.add_handler(CallbackQueryHandler(action_cmd_network, pattern="^cmd_network$"))
    app.add_handler(CallbackQueryHandler(action_cmd_stats, pattern="^cmd_stats$"))
    app.add_handler(CallbackQueryHandler(action_cmd_boost, pattern="^cmd_boost$"))
    app.add_handler(CallbackQueryHandler(action_cmd_earnings, pattern="^cmd_earnings$"))
    app.add_handler(CallbackQueryHandler(action_cmd_news, pattern="^cmd_news$"))
    app.add_handler(CallbackQueryHandler(action_force_join_check, pattern="^force_join_check$"))
    app.add_handler(CallbackQueryHandler(action_pay_crypto, pattern=r"^pay_crypto_"))
    app.add_handler(CallbackQueryHandler(action_pay_paid, pattern=r"^pay_paid_"))
    app.add_handler(CallbackQueryHandler(action_pay_cancel, pattern="^pay_cancel$"))
    app.add_handler(CallbackQueryHandler(action_boost, pattern=r"^boost_(2x|5x)_(1hr|6hr|24hr)$"))
    app.add_handler(CallbackQueryHandler(action_coin_switch, pattern=r"^sw_"))
    app.add_handler(CallbackQueryHandler(action_pool_join, pattern="^pool_join"))
    app.add_handler(CallbackQueryHandler(action_pool_leave, pattern="^pool_leave$"))
    app.add_handler(CallbackQueryHandler(action_upg, pattern=r"^upg_(Starter|Pro|Elite|VIP)$"))
    app.add_handler(CallbackQueryHandler(action_wd_coin, pattern=r"^wd_coin_"))
    app.add_handler(CallbackQueryHandler(action_pay_crypto_select, pattern=r"^pay_select_"))

    # Text/photo handler — must be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=1)
    app.add_handler(MessageHandler(filters.PHOTO, text_handler), group=1)

    # Setup admin panel
    from admin import setup_admin
    setup_admin(app)

    # Global error handler — surfaces exceptions to the user instead of failing silently
    app.add_error_handler(error_handler)

    return app
