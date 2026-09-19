"""
CryptoMinerPro — Telegram Admin Panel
All bot functions are editable from here. No web panel needed.
"""
import os
import asyncio
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)

from models import get_db
from models.user import User
from models.withdrawal import Withdrawal
from models.payment_request import PaymentRequest
from models.mining_session import MiningSession
from models.pool import Pool
from bot import invalidate_user_cache
from config.constants import (
    UPGRADE_PLANS, PLAN_ORDER, COIN_PRICES, COIN_MULTIPLIERS,
    COIN_INFO, DEFAULT_SETTINGS, fmt, p_icon, save_settings,
    entry_ref, entry_link, entry_label,
    ROLE_OWNER, ROLE_MANAGER, ROLE_MODERATOR, ROLE_LABELS,
    get_role, is_staff, is_owner, can, all_staff_ids, market_price,
    paint_button_label,
)
from config.live_prices import live_price, refresh_live_prices

# Conversation states
(WAIT_BROADCAST_MSG, WAIT_AIRDROP_AMOUNT, WAIT_COIN_PRICE,
 WAIT_USER_SEARCH, WAIT_BAN_REASON, WAIT_SETTINGS_VALUE, WAIT_REJECT_REASON) = range(7)
# Extra states for the 'add a chat ID to a link-only entry' conversations
WAIT_FJ_ID, WAIT_PG_ID = 10, 11
# Extra states: rename a force-join/proof entry, edit the force-join screen text
WAIT_FJ_NAME, WAIT_PG_NAME, WAIT_FJ_TEXT = 12, 13, 14

BOT_REF = None  # set after bot creation


def is_admin(user_id):
    """Panel access: owners, managers and moderators all open /admin."""
    return is_staff(user_id)


def _ui_style_label():
    return {
        "smallcaps": "🅰️ Small Caps",
        "title": "🅱️ Title Case",
        "plain": "⬜ Plain (off)",
    }.get(DEFAULT_SETTINGS.get("ui_style", "smallcaps"), "🅰️ Small Caps")


def admin_keyboard(uid=None):
    """Color-coded control panel grid (🟢 green = money/actions, 🔵 blue =
    tools/info, 🟡 yellow = status, 🔴 red = staff/danger/close)."""
    rows = [
        [
            InlineKeyboardButton("🟢 📊 STATISTICS", callback_data="adm_dashboard"),
            InlineKeyboardButton("🔵 👥 USERS", callback_data="adm_users"),
        ],
        [
            InlineKeyboardButton("🟢 💸 WITHDRAWALS", callback_data="adm_withdrawals"),
            InlineKeyboardButton("🔵 💳 PAYMENTS", callback_data="adm_payments"),
        ],
        [
            InlineKeyboardButton("🟢 📢 BROADCAST", callback_data="adm_broadcast"),
            InlineKeyboardButton("🔵 🪂 AIRDROP", callback_data="adm_airdrop"),
        ],
        [
            InlineKeyboardButton("🟢 ⚙️ SETTINGS", callback_data="adm_settings"),
            InlineKeyboardButton("🔵 🪙 COIN PRICES", callback_data="adm_prices"),
        ],
        [
            InlineKeyboardButton("🔵 📢 FORCE JOIN", callback_data="adm_forcejoin"),
            InlineKeyboardButton("🔵 📨 PROOF GROUPS", callback_data="adm_proofgroups"),
        ],
        [
            InlineKeyboardButton("🔵 📈 MINING", callback_data="adm_mining"),
            InlineKeyboardButton("🟡 🔒 MAINTENANCE", callback_data="adm_maintenance"),
        ],
        [
            InlineKeyboardButton("🔵 🏊 POOLS", callback_data="adm_pools"),
            InlineKeyboardButton("🟢 📤 TEST PROOF", callback_data="adm_test_proof"),
        ],
    ]
    if uid is None or can(uid, "manage_staff"):
        rows.append([InlineKeyboardButton("🔴 👮 MANAGE STAFF", callback_data="adm_staff")])
    rows.append([InlineKeyboardButton(f"🎨 UI STYLE: {_ui_style_label()}", callback_data="adm_font_menu")])
    bc = DEFAULT_SETTINGS.get("button_colors", True)
    rows.append([InlineKeyboardButton(
        f"{'🟢' if bc else '🔴'} 🎨 BUTTON COLORS: {'ON' if bc else 'OFF'}",
        callback_data="adm_btncolor_panel")])
    rows.append([InlineKeyboardButton("🔴 ❌ CLOSE", callback_data="adm_close")])
    return InlineKeyboardMarkup(rows)


def _count_buttons(rows):
    """Count how the buttons in a keyboard (list of rows) would be painted."""
    counts = {"success": 0, "danger": 0, "primary": 0, "total": 0}
    for row in rows:
        for btn in row:
            style, _ = paint_button_label(btn.text)
            counts[style] = counts.get(style, 0) + 1
            counts["total"] += 1
    return counts


def _btncolor_note(rows, enabled, scope):
    """Short confirmation line shown after toggling colors — real counts."""
    s = _count_buttons(rows)
    if enabled:
        return (
            f"🎨 Button colors: 🟢 ON — {s['total']} buttons painted "
            f"(🟢 {s['success']} · 🔴 {s['danger']} · 🔵 {s['primary']}) on {scope}. "
            f"All new messages in every chat paint automatically; older messages "
            f"update next time they are re-sent or edited."
        )
    return (
        f"🎨 Button colors: 🔴 OFF — {s['total']} buttons on {scope} reverted "
        f"to plain (color-chip stickers shown again). Nothing new is painted "
        f"until you switch it back on."
    )


# ═══════════════════════════════════════════════════════════════
# 🖌️ Per-button color picker (green / red / blue per button)
# ═══════════════════════════════════════════════════════════════
# Catalog of the buttons the picker can repaint. Keys are callback_data
# values (exact or a prefix pattern ending in "*"), saved to settings
# under "button_color_overrides" and applied at serialization time — a
# pinned color wins even when the global "Colored Buttons" toggle is off.
BTN_PICKER_GROUPS = [
    ("👥 User Menu", [
        ("cmd_mine", "🔥 MINE"), ("cmd_balance", "💲 BALANCE"),
        ("cmd_upgrade", "⭐ UPGRADE"), ("cmd_withdraw", "💰 WITHDRAW"),
        ("cmd_daily", "🔖 DAILY"), ("cmd_referral", "👥 REFERRAL"),
        ("cmd_stats", "📊 STATS"), ("cmd_achievements", "🏆 ACHIEVEMENTS"),
        ("cmd_boost", "⚡ BOOSTS"), ("cmd_myplan", "💳 MY PLAN"),
        ("cmd_network", "🌐 NETWORK"), ("cmd_news", "📰 NEWS"),
        ("cmd_help", "ℹ️ HELP"), ("cmd_back", "⬅️ BACK"),
        ("cmd_admin", "👑 ADMIN PANEL"),
    ]),
    ("⛏️ Mining & Pools", [
        ("stopmine", "⏹️ STOP MINING"), ("pool_join", "🌐 JOIN POOL"),
        ("pool_join_vip", "💎 JOIN VIP POOL"), ("pool_leave", "🚪 LEAVE POOL"),
    ]),
    ("💳 Payments & Verify", [
        ("pay_paid_*", "✅ I'VE PAID"), ("pay_cancel", "❌ CANCEL"),
        ("force_join_check", "✅ I'VE JOINED"),
        ("adm_pay_verify_*", "✅ Verify Payment"),
        ("adm_pay_reject_*", "❌ Reject Payment"),
        ("adm_wd_approve_*", "✅ Approve Withdrawal"),
        ("adm_wd_reject_*", "❌ Reject Withdrawal"),
    ]),
    ("👑 Admin Panel", [
        ("adm_dashboard", "📊 STATISTICS"), ("adm_users", "👥 USERS"),
        ("adm_withdrawals", "💸 WITHDRAWALS"), ("adm_payments", "💳 PAYMENTS"),
        ("adm_broadcast", "📢 BROADCAST"), ("adm_airdrop", "🪂 AIRDROP"),
        ("adm_settings", "⚙️ SETTINGS"), ("adm_prices", "🪙 COIN PRICES"),
        ("adm_forcejoin", "📢 FORCE JOIN"), ("adm_proofgroups", "📨 PROOF GROUPS"),
        ("adm_mining", "📈 MINING"), ("adm_maintenance", "🔒 MAINTENANCE"),
        ("adm_pools", "🏊 POOLS"), ("adm_test_proof", "📤 TEST PROOF"),
        ("adm_staff", "👮 MANAGE STAFF"), ("adm_close", "❌ CLOSE"),
        ("admin_back", "⬅️ MENU BACK"),
    ]),
]

_BP_STYLE_CHIP = {"success": "🟢", "danger": "🔴", "primary": "🔵"}
_BP_STYLE_NAME = {"success": "Green", "danger": "Red", "primary": "Blue"}
_BP_CYCLE = ("", "success", "danger", "primary")  # tap to cycle


async def adm_btncolor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🖌️ Button Colors — tap a row to cycle its color (Auto → 🟢 → 🔴 → 🔵)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    ov = DEFAULT_SETTINGS.setdefault("button_color_overrides", {})
    text = (
        "🖌️ *Button Colors*\n\n"
        "Tap a row to cycle its color: Auto → 🟢 Green → 🔴 Red → 🔵 Blue.\n"
        "Pinned colors are applied even when the global 🎨 Colored Buttons "
        "toggle is OFF (great for keeping STOP/PAID buttons in their state colors).\n\n"
    )
    for title, _items in BTN_PICKER_GROUPS:
        text += f"▶ {title}\n"
    rows = []
    for _title, items in BTN_PICKER_GROUPS:
        for key, label in items:
            cur = ov.get(key, "")
            chip = _BP_STYLE_CHIP.get(cur, "⚪")
            name = _BP_STYLE_NAME.get(cur, "Auto")
            rows.append([InlineKeyboardButton(f"{label} — {chip} {name}", callback_data=f"adm_bclr_{key}")])
    rows.append([InlineKeyboardButton("🔄 Reset all to Auto", callback_data="adm_bclr_reset")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))


async def adm_bclr_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    key = query.data[len("adm_bclr_"):]
    if not key:
        return await query.answer("Invalid button")
    ov = DEFAULT_SETTINGS.setdefault("button_color_overrides", {})
    cur = ov.get(key, "")
    nxt = _BP_CYCLE[(_BP_CYCLE.index(cur) + 1) % len(_BP_CYCLE)] if cur in _BP_CYCLE else "success"
    if nxt:
        ov[key] = nxt
    else:
        ov.pop(key, None)
    save_settings()
    chip = _BP_STYLE_CHIP.get(nxt, "⚪")
    await query.answer(f"{key}: {chip} {_BP_STYLE_NAME.get(nxt, 'Auto')}")
    await adm_btncolor_menu(update, context)


async def adm_bclr_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    DEFAULT_SETTINGS["button_color_overrides"] = {}
    save_settings()
    await query.answer("All buttons back to Auto")
    await adm_btncolor_menu(update, context)


# ═══════════════════════════════════════════════════════════════
# /admin — Main admin panel
# ═══════════════════════════════════════════════════════════════
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "    👑 *ADMIN CONTROL PANEL*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=admin_keyboard(update.effective_user.id),
    )


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    await query.edit_message_text(
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "    👑 *ADMIN CONTROL PANEL*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n",
        parse_mode="Markdown",
        reply_markup=admin_keyboard(query.from_user.id),
    )


async def adm_font_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Per-admin personal toggle: show the styled font in THIS admin's chat."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    uid = query.from_user.id
    lst = DEFAULT_SETTINGS.setdefault("styled_admins", [])
    on = uid in lst
    if on:
        lst.remove(uid)
    else:
        lst.append(uid)
    save_settings()
    await query.answer(f"My panel font: {'✨ styled ON' if not on else 'plain OFF'}")
    await adm_font_menu(update, context)


async def adm_font_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """UI style picker: choose the global user-facing font style."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    uid = query.from_user.id
    cur = DEFAULT_SETTINGS.get("ui_style", "smallcaps")
    mine_on = uid in (DEFAULT_SETTINGS.get("styled_admins") or [])
    text = (
        f"🎨 *UI Style — user-facing font*\n\n"
        f"Current: *{_ui_style_label()}*\n\n"
        f"🅰️ Small Caps — the Unicode font: `ᴅᴜᴇ ᴛᴏ ɪssᴜᴇꜱ`\n"
        f"🅱️ Title Case — clean look: `Your Files Will Be Deleted`\n"
        f"⬜ Plain — normal text (font off)\n\n"
        f"This applies to every message users see. Your own panel stays plain "
        f"unless you switch your personal one below."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🅰️ Small Caps", callback_data="adm_style_smallcaps"),
         InlineKeyboardButton("🅱️ Title Case", callback_data="adm_style_title")],
        [InlineKeyboardButton("⬜ Plain (off)", callback_data="adm_style_plain")],
        [InlineKeyboardButton(f"👤 MY PANEL FONT: {'✨ ON' if mine_on else 'OFF'}", callback_data="adm_font_toggle")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


async def adm_style_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a global UI style (smallcaps / title / plain)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    if not can(query.from_user.id, "manage_settings"):
        return await query.answer("⛔ Only owners & managers can change the UI style")
    style = query.data.replace("adm_style_", "")
    if style not in ("smallcaps", "title", "plain"):
        return await query.answer("Unknown style")
    DEFAULT_SETTINGS["ui_style"] = style
    await query.answer(f"UI style set to: {style}")
    await adm_font_menu(update, context)


async def adm_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close (delete) the control panel message."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer("Closed")
    try:
        await query.message.delete()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# A. Dashboard
# ═══════════════════════════════════════════════════════════════
async def adm_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer("Loading...")

    db = get_db()
    total_users = await db["users"].count_documents({})
    active_24h = await User.count_active()
    pending_w = await db["withdrawals"].count_documents({"status": {"$in": ["pending", "processing"]}})
    pending_p = await db["payment_requests"].count_documents({"status": {"$in": ["pending", "screenshot_received"]}})

    # Total mined
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$total_mined"}}}]
    result = await db["users"].aggregate(pipeline).to_list(1)
    total_mined = result[0]["total"] if result else 0

    # Plan distribution
    plan_pipe = [{"$group": {"_id": "$plan", "count": {"$sum": 1}}}]
    plan_res = await db["users"].aggregate(plan_pipe).to_list(10)
    plan_dist = {p["_id"]: p["count"] for p in plan_res}
    top_plan = max(plan_dist, key=plan_dist.get) if plan_dist else "Free"

    text = (
        f"📊 *Dashboard*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users: *{total_users:,}*\n"
        f"🟢 Active (24h): *{active_24h:,}*\n"
        f"💰 Total Mined: *{fmt(total_mined)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💸 Pending Withdrawals: *{pending_w}*\n"
        f"💳 Pending Payments: *{pending_p}*\n"
        f"📊 Top Plan: *{top_plan}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ Maintenance: {'ON' if os.getenv('ENABLE_MAINTENANCE') == 'true' else 'OFF'}\n"
        f"🔗 Force Join: {'ON' if DEFAULT_SETTINGS.get('force_join_enabled') else 'OFF'}\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="adm_dashboard")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════
# A2. Staff management — owners / managers / moderators
# ═══════════════════════════════════════════════════════════════
async def adm_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    staff = DEFAULT_SETTINGS.get("staff") or {}
    lines = []
    for uid in all_staff_ids():
        role = get_role(uid)
        origin = ""
        if role == ROLE_OWNER and str(uid) not in staff:
            origin = " (.env)"
        lines.append(f"• {ROLE_LABELS.get(role, role)} — `{uid}`{origin}")
    text = (
        f"👥 *Staff & Permissions*\n\n" + ("\n".join(lines) if lines else "_(no staff yet)_") +
        "\n\n━━━━━━━━━━━━━━━━━━━\n"
        f"👑 Owner — full access\n"
        f"🛠️ Manager — almost full (no wallet edit, no staff changes)\n"
        f"👁️ Moderator — view only: sees payments/withdrawals/users but cannot approve anything\n"
        f"\n*Only staff can approve payments & withdrawals:* 👑 owners and 🛠️ managers.\n"
        f"Managers approve; moderators only view."
    )
    buttons = []
    if can(query.from_user.id, "manage_staff"):
        buttons.append([InlineKeyboardButton("➕ Add Staff", callback_data="adm_staff_add")])
        removables = [str(uid) for uid in staff if str(uid).lstrip("-").isdigit()]
        if removables:
            buttons.append([InlineKeyboardButton("➖ Remove Staff", callback_data="adm_staff_remove")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_staff_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_staff"):
        return await query.answer("⛔ Only owners can manage staff")
    await query.answer()
    await query.edit_message_text(
        "➕ *Add staff member*\n\nSend the user's numeric ID and role on one line:\n\n"
        "`123456789 manager`  → almost-full access (approves payments/withdrawals)\n"
        "`123456789 moderator` → view only\n"
        "`123456789 owner`  → full access\n\n"
        "Find a user's ID by asking them to send /start to the bot and checking "
        "the ID shown in your admin views (or use @userinfobot)."
    )
    return WAIT_SETTINGS_VALUE


async def adm_staff_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "manage_staff"):
        await update.message.reply_text("⛔ Only owners can manage staff.")
        return ConversationHandler.END
    parts = update.message.text.strip().split(None, 1)
    if len(parts) != 2 or not str(parts[0]).lstrip("-").isdigit():
        await update.message.reply_text(
            "❌ Format: `user_id role` — e.g. `123456789 manager`.\n"
            "Role must be: `owner`, `manager` or `moderator`.",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
        )
        return ConversationHandler.END
    uid = int(parts[0])
    role = parts[1].strip().lower()
    if role not in (ROLE_OWNER, ROLE_MANAGER, ROLE_MODERATOR):
        await update.message.reply_text(
            f"❌ Unknown role `{role}`. Use `owner`, `manager` or `moderator`.",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
        )
        return ConversationHandler.END
    staff = DEFAULT_SETTINGS.setdefault("staff", {})
    staff[str(uid)] = role
    save_settings()
    await update.message.reply_text(
        f"✅ *Staff saved!*\n\n`{uid}` is now: *{role}*\n\n"
        f"They can open /admin immediately. Screenshots & withdrawals will be "
        f"forwarded to them according to their role.",
        parse_mode="Markdown",
        reply_markup=admin_keyboard(),
    )
    return ConversationHandler.END


async def adm_staff_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_staff"):
        return await query.answer("⛔ Only owners can manage staff")
    staff = DEFAULT_SETTINGS.get("staff") or {}
    if not staff:
        return await query.answer("No panel-added staff to remove")
    buttons = [[InlineKeyboardButton(f"➖ {get_role(int(uid))} — {uid}", callback_data=f"adm_staff_del_{uid}")]
               for uid in staff]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_staff")])
    await query.answer()
    await query.edit_message_text("➖ Remove staff member:", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_staff_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_staff"):
        return await query.answer("⛔ Only owners can manage staff")
    uid = query.data.replace("adm_staff_del_", "")
    staff = DEFAULT_SETTINGS.get("staff") or {}
    if uid in staff:
        del staff[uid]
        save_settings()
    await query.answer("Removed")
    await adm_staff(update, context)


# ═══════════════════════════════════════════════════════════════
# B. Users
# ═══════════════════════════════════════════════════════════════
async def adm_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    context.user_data["user_page"] = 0
    await show_users_page(query, context)


async def show_users_page(query, context):
    page = context.user_data.get("user_page", 0)
    db = get_db()
    skip = page * 5
    cursor = db["users"].find({}).sort("created_at", -1).skip(skip).limit(5)
    users = await cursor.to_list(5)
    total = await db["users"].count_documents({})
    total_pages = max(1, (total + 4) // 5)

    text = f"👥 *Users* (Page {page+1}/{total_pages})\n\n"
    buttons = []
    for u in users:
        uid = u["user_id"]
        name = u.get("username") or u.get("first_name") or str(uid)
        plan = u.get("plan", "Free")
        bal = u.get("balance", 0)
        text += f"• @{name} (ID: `{uid}`)\n  {p_icon(plan)} {plan} | 💰 {fmt(bal)}\n"
        buttons.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"adm_user_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="adm_users_prev"))
    if (page + 1) * 5 < total:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data="adm_users_next"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔍 Search User", callback_data="adm_user_search")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_users_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["user_page"] = context.user_data.get("user_page", 0) + 1
    await query.answer()
    await show_users_page(query, context)


async def adm_users_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["user_page"] = max(0, context.user_data.get("user_page", 0) - 1)
    await query.answer()
    await show_users_page(query, context)


async def adm_user_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Send the user ID or username to search:")
    return WAIT_USER_SEARCH


async def adm_user_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        uid = int(text)
        user = await User.find_one(uid)
    except ValueError:
        db = get_db()
        doc = await db["users"].find_one({"username": text.lstrip("@")})
        user = User(doc) if doc else None

    if not user:
        await update.message.reply_text("❌ User not found.", reply_markup=admin_keyboard())
        return ConversationHandler.END

    await show_user_detail(update, user)
    return ConversationHandler.END


async def show_user_detail(update, user: User):
    d = user.to_dict()
    uid = user.user_id
    name = user.username or user.first_name or str(uid)
    text = (
        f"👤 *User Detail*\n\n"
        f"ID: `{uid}`\nName: @{name}\n"
        f"Plan: {p_icon(user.plan)} *{user.plan}*\n"
        f"💰 Balance: *{fmt(user.balance)}*\n"
        f"⚡ Hash Rate: *{user.hash_rate:.1f} H/s*\n"
        f"📈 Total Mined: *{fmt(user.total_mined)}*\n"
        f"🪙 Coin: *{user.mining_coin}*\n"
        f"👥 Referrals: *{user.referral_count}*\n"
        f"⛏️ Sessions: *{user.sessions}*\n"
        f"🚫 Banned: *{user.banned}*\n"
    )
    if user.banned and user.ban_reason:
        text += f"📝 Ban Reason: {user.ban_reason}\n"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Edit Balance", callback_data=f"adm_bal_{uid}"),
            InlineKeyboardButton("⚡ Edit Hash", callback_data=f"adm_hash_{uid}"),
        ],
        [
            InlineKeyboardButton("📦 Change Plan", callback_data=f"adm_plan_{uid}"),
            InlineKeyboardButton("🚫 Ban/Unban", callback_data=f"adm_ban_{uid}"),
        ],
        [
            InlineKeyboardButton("🔄 Reset User", callback_data=f"adm_reset_{uid}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="adm_users")],
    ])
    target = update.message if hasattr(update, "message") and update.message else update
    await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def adm_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[-1])
    user = await User.find_one(uid)
    if not user:
        return await query.answer("User not found")
    await query.answer()
    await show_user_detail(query, user)


# ── Edit Balance ──
async def adm_bal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[-1])
    context.user_data["edit_uid"] = uid
    await query.answer()
    await query.edit_message_text(
        f"💰 Editing balance for user `{uid}`\n\nSend the amount to ADD (or negative to subtract):",
        parse_mode="Markdown"
    )
    return WAIT_SETTINGS_VALUE


async def adm_bal_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "manage_users"):
        await update.message.reply_text("⛔ Only owners & managers can edit users.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    uid = context.user_data.get("edit_uid")
    amount = float(update.message.text.strip())
    user = await User.find_one(uid)
    if not user:
        await update.message.reply_text("❌ User not found.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    old = user.balance
    user.balance = max(0, user.balance + amount)
    await user.save()
    await update.message.reply_text(
        f"✅ Balance updated: {fmt(old)} → {fmt(user.balance)}",
        reply_markup=admin_keyboard()
    )
    return ConversationHandler.END


# ── Edit Hash Rate ──
async def adm_hash_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[-1])
    context.user_data["edit_uid"] = uid
    await query.answer()
    await query.edit_message_text(
        f"⚡ Set hash rate for user `{uid}`\n\nSend the new base hash rate (H/s):",
        parse_mode="Markdown"
    )
    return WAIT_SETTINGS_VALUE


async def adm_hash_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "manage_users"):
        await update.message.reply_text("⛔ Only owners & managers can edit users.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    uid = context.user_data.get("edit_uid")
    hr = float(update.message.text.strip())
    user = await User.find_one(uid)
    if not user:
        await update.message.reply_text("❌ User not found.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    user.base_hash_rate = max(1, hr)
    user.calculate_effective_hash_rate()
    await user.save()
    await update.message.reply_text(
        f"✅ Hash rate set to {user.hash_rate:.1f} H/s",
        reply_markup=admin_keyboard()
    )
    return ConversationHandler.END


# ── Change Plan ──
async def adm_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[-1])
    context.user_data["edit_uid"] = uid
    buttons = [[InlineKeyboardButton(f"{p_icon(p)} {p}", callback_data=f"adm_planset_{uid}_{p}")] for p in PLAN_ORDER]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"adm_user_{uid}")])
    await query.answer()
    await query.edit_message_text(f"📦 Select plan for user `{uid}`:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_plan_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_users"):
        return await query.answer("⛔ Only owners & managers can change plans")
    parts = query.data.split("_")
    uid = int(parts[2])
    plan_name = parts[3]
    if plan_name not in UPGRADE_PLANS:
        return await query.answer("Invalid plan")
    user = await User.find_one(uid)
    if not user:
        return await query.answer("User not found")
    plan = UPGRADE_PLANS[plan_name]
    user.plan = plan_name
    user.plan_multiplier = plan["multiplier"]
    user.base_hash_rate = plan["hash_rate"]
    user.calculate_effective_hash_rate()
    await user.save()
    await query.answer(f"✅ Plan set to {plan_name}")
    await show_user_detail(query, user)


# ── Ban/Unban ──
async def adm_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[-1])
    user = await User.find_one(uid)
    if not user:
        return await query.answer("User not found")
    if not can(query.from_user.id, "manage_users"):
        return await query.answer("⛔ Only owners & managers can manage users")
    user.banned = not user.banned
    user.ban_reason = "Banned by admin" if user.banned else None
    await user.save()
    status = "🚫 BANNED" if user.banned else "✅ UNBANNED"
    await query.answer(f"{status}: {uid}")
    # Notify user
    if BOT_REF:
        try:
            await BOT_REF.send_message(uid, f"{'🚫 Your account has been suspended.' if user.banned else '✅ Your account has been restored.'}")
        except Exception:
            pass
    await show_user_detail(query, user)


# ── Reset User ──
async def adm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[-1])
    user = await User.find_one(uid)
    if not user:
        return await query.answer("User not found")
    if not can(query.from_user.id, "manage_users"):
        return await query.answer("⛔ Only owners & managers can manage users")
    user.balance = 0
    user.total_mined = 0
    user.daily_mined = 0
    user.weekly_mined = 0
    user.monthly_mined = 0
    user.hash_rate = 1
    user.base_hash_rate = 1
    user.plan = "Free"
    user.plan_multiplier = 1
    user.achievements = []
    user.boosts = []
    user.sessions = 0
    user.total_mining_time = 0
    user.daily_streak = 0
    await user.save()
    await query.answer("✅ User reset")
    await show_user_detail(query, user)


# ═══════════════════════════════════════════════════════════════
# C. Withdrawals
# ═══════════════════════════════════════════════════════════════

def _is_media_message(msg):
    """True when a message carries media (photo/video/doc…) instead of plain text."""
    if msg is None:
        return False
    return bool(
        getattr(msg, "photo", None) or getattr(msg, "document", None)
        or getattr(msg, "video", None) or getattr(msg, "animation", None)
        or getattr(msg, "audio", None) or getattr(msg, "voice", None)
    )


async def _edit_or_send(query, text, kb=None, parse_mode="Markdown"):
    """Edit the query message in place — but Telegram forbids editMessageText on
    media messages, so those get their caption edited instead."""
    if _is_media_message(query.message):
        try:
            await query.message.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=kb)
        except Exception:
            # Photo may be unsendable/changed — fall back to a fresh message
            await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=kb)
    else:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=kb)


async def adm_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    pending = await Withdrawal.find_pending()
    text = f"💸 *Pending Withdrawals* ({len(pending)})\n\n"
    buttons = []
    for w in pending[:10]:
        text += f"• `{w.user_id}` — {w.amount} {w.coin} ({w.status})\n"
        buttons.append([InlineKeyboardButton(
            f"✅ {w.amount} {w.coin} — ID:{w.user_id}",
            callback_data=f"adm_wd_{w.id}"
        )])
    if not pending:
        text += "No pending withdrawals."
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await _edit_or_send(query, text, InlineKeyboardMarkup(buttons))


async def adm_wd_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    wid = query.data.split("_")[-1]
    from bson import ObjectId
    w = await Withdrawal.find_by_id(ObjectId(wid))
    if not w:
        return await query.answer("Not found")
    text = (
        f"💸 *Withdrawal Detail*\n\n"
        f"User: `{w.user_id}`\n"
        f"Amount: *{w.amount} {w.coin}*\n"
        f"Address: `{w.address}`\n"
        f"Status: *{w.status}*\n"
        f"Plan: *{w.plan}*\n"
        f"Requested: {w.requested_at}\n"
    )
    buttons = [
        [InlineKeyboardButton("✅ Approve", callback_data=f"adm_wd_approve_{wid}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"adm_wd_reject_{wid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="adm_withdrawals")],
    ]
    await query.answer()
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_wd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    from bson import ObjectId
    wid = query.data.split("_")[-1]
    w = await Withdrawal.find_by_id(ObjectId(wid))
    if not w:
        return await query.answer("Not found")
    if w.status in ("processing", "approved", "rejected"):
        return await query.answer("⏳ Already handled")
    if not can(query.from_user.id, "approve_withdrawals"):
        return await query.answer("⛔ Only owners & managers can approve withdrawals")

    # Deduct balance NOW (on approval, not on submission)
    user = await User.find_one(w.user_id)
    if not user:
        return await query.answer("User not found")
    if user.balance < w.amount:
        return await query.answer("❌ User has insufficient balance!")
    user.balance -= w.amount
    await user.save()

    tx = generate_fake_tx(w.coin)
    w.status = "processing"
    w.tx_hash = tx
    w.add_processing_step("approved", f"Approved & balance deducted ({w.amount} {w.coin})")
    w.add_processing_step("processing_started", f"TX: {tx}")
    await w.save()
    # Notify user
    if BOT_REF:
        try:
            await BOT_REF.send_message(
                w.user_id,
                f"✅ *Withdrawal Approved & Processing!*\n\n💰 *{w.amount} {w.coin}* is being processed.\n🔗 TX: `{tx[:20]}...`\n⏰ Est: ~15 min",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    # Forward proof to groups
    await forward_withdrawal_proof(w)
    await query.answer("✅ Approved & balance deducted")
    await adm_withdrawals(update, context)


async def adm_wd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    from bson import ObjectId
    wid = query.data.split("_")[-1]
    w = await Withdrawal.find_by_id(ObjectId(wid))
    if not w:
        return await query.answer("Not found")
    if w.status in ("processing", "approved", "rejected"):
        return await query.answer("⏳ Already handled")
    if not can(query.from_user.id, "approve_withdrawals"):
        return await query.answer("⛔ Only owners & managers can approve withdrawals")
    w.status = "rejected"
    w.add_processing_step("rejected", "Rejected by admin")
    await w.save()
    # Refund
    user = await User.find_one(w.user_id)
    if user:
        user.balance += w.amount
        await user.save()
    invalidate_user_cache(user.user_id)
    if BOT_REF:
        try:
            await BOT_REF.send_message(w.user_id, f"❌ *Withdrawal Rejected*\n\n💰 {w.amount} {w.coin} has been refunded.", parse_mode="Markdown")
        except Exception:
            pass
    await query.answer("❌ Rejected")
    await adm_withdrawals(update, context)


# ═══════════════════════════════════════════════════════════════
# D. Payments
# ═══════════════════════════════════════════════════════════════
async def adm_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    pending = await PaymentRequest.find_pending()
    text = f"💳 *Pending Payments* ({len(pending)})\n\n"
    buttons = []
    for p in pending[:10]:
        text += f"• `{p.user_id}` — {p.plan} ({p.amount} {p.coin}) [{p.status}]\n"
        buttons.append([InlineKeyboardButton(
            f"💳 {p.plan} — ID:{p.user_id}",
            callback_data=f"adm_pay_{p.id}"
        )])
    if not pending:
        text += "No pending payments."
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await _edit_or_send(query, text, InlineKeyboardMarkup(buttons))


async def adm_pay_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    from bson import ObjectId
    pid = query.data.split("_")[-1]
    p = await PaymentRequest.find_by_id(ObjectId(pid))
    if not p:
        return await query.answer("Not found")
    text = (
        f"💳 *Payment Detail*\n\n"
        f"User: `{p.user_id}`\nPlan: *{p.plan}*\n"
        f"Amount: *{p.amount} {p.coin}*\nStatus: *{p.status}*\n"
    )
    if p.rejection_reason:
        text += f"\n📝 Rejection reason: _{p.rejection_reason}_\n"
    buttons = [
        [InlineKeyboardButton("✅ Verify", callback_data=f"adm_pay_verify_{pid}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"adm_pay_reject_{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="adm_payments")],
    ]
    await query.answer()
    # If a screenshot exists, send it as a photo so the admin can actually SEE it
    if p.screenshot_file_id:
        try:
            await context.bot.send_photo(
                query.message.chat.id,
                p.screenshot_file_id,
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            try:
                await query.edit_message_text(
                    "📸 *Screenshot and payment details sent above.*", parse_mode="Markdown"
                )
            except Exception:
                pass
            return
        except Exception:
            pass  # fall through to text-only view if the photo fails
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_pay_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    from bson import ObjectId
    pid = query.data.split("_")[-1]
    p = await PaymentRequest.find_by_id(ObjectId(pid))
    if not p:
        return await query.answer("Not found")
    if p.status == "verified":
        return await query.answer("✅ Already verified")
    if p.status == "rejected":
        return await query.answer("❌ Payment was rejected")
    if not can(query.from_user.id, "approve_payments"):
        return await query.answer("⛔ Only owners & managers can approve payments")
    p.status = "verified"
    p.verified_at = datetime.now(timezone.utc)
    p.tx_hash = generate_fake_tx(p.coin)
    await p.save()
    # Upgrade user
    user = await User.find_one(p.user_id)
    if user:
        plan = UPGRADE_PLANS.get(p.plan, {})
        user.plan = p.plan
        user.plan_multiplier = plan.get("multiplier", 1)
        user.base_hash_rate = plan.get("hash_rate", 1)
        user.calculate_effective_hash_rate()
        user.pending_payment_id = None
        await user.save()
    invalidate_user_cache(user.user_id)
    if BOT_REF:
        try:
            plan = UPGRADE_PLANS.get(p.plan, {})
            await BOT_REF.send_message(
                p.user_id,
                f"🎉 *Payment Verified!*\n\n{plan.get('icon', '📦')} You've been upgraded to *{p.plan}*!\n⚡ Hash Rate: *{plan.get('hash_rate', '?')} H/s*",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await query.answer("✅ Verified")
    await adm_payments(update, context)


async def adm_pay_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start rejection — ask the admin for a reason first."""
    query = update.callback_query
    from bson import ObjectId
    pid = query.data.replace("adm_pay_reject_", "")
    p = await PaymentRequest.find_by_id(ObjectId(pid))
    if not p:
        return await query.answer("Not found")
    if p.status in ("verified", "rejected"):
        return await query.answer("⏳ Payment already " + p.status)
    if not can(query.from_user.id, "approve_payments"):
        return await query.answer("⛔ Only owners & managers can approve payments")
    context.user_data["reject_pid"] = pid
    await query.answer()
    await _edit_or_send(
        query,
        f"❌ *Reject Payment*\n\nUser: `{p.user_id}` | Plan: *{p.plan}* | {p.amount} {p.coin}\n\n"
        f"Send the reason for rejecting. The user will see this reason.\n\n"
        f"Type the reason, or send /skip to reject without a reason.",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Cancel", callback_data="adm_payments")]
        ]),
    )
    return WAIT_REJECT_REASON


async def adm_pay_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finish rejection with the admin-provided reason."""
    message = update.message
    pid = context.user_data.get("reject_pid")
    if not pid:
        await message.reply_text("❌ No pending rejection. Use /admin.")
        return ConversationHandler.END
    reason = None
    if message.text and not message.text.startswith("/skip"):
        reason = message.text.strip()
    from bson import ObjectId
    p = await PaymentRequest.find_by_id(ObjectId(pid))
    if not p:
        await message.reply_text("❌ Payment not found.")
        return ConversationHandler.END
    p.status = "rejected"
    if reason:
        p.rejection_reason = reason
    await p.save()
    user = await User.find_one(p.user_id)
    if user:
        user.pending_payment_id = None
        user.waiting_for_screenshot = False
        await user.save()
    invalidate_user_cache(p.user_id)
    if BOT_REF:
        try:
            txt = f"❌ *Payment Rejected*\n\nYour payment for *{p.plan}* could not be verified."
            if reason:
                txt += f"\n\n📝 Reason: _{reason}_\n\nContact admin if you think this is a mistake."
            await BOT_REF.send_message(p.user_id, txt, parse_mode="Markdown")
        except Exception:
            pass
    await message.reply_text(
        f"❌ Payment rejected" + (f" — reason: _{reason}_" if reason else " without a reason") + ".\nThe user has been notified.",
        parse_mode="Markdown",
        reply_markup=admin_keyboard(),
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# E. Broadcast
# ═══════════════════════════════════════════════════════════════
async def adm_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "broadcast"):
        return await query.answer("⛔ Only owners & managers can broadcast")
    await query.answer()
    await query.edit_message_text("📢 Send the broadcast message:")
    return WAIT_BROADCAST_MSG


async def adm_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "broadcast"):
        await update.message.reply_text("⛔ Only owners & managers can broadcast.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    msg = update.message.text
    users = await User.get_all_active()
    sent, failed = 0, 0
    for u in users:
        try:
            if BOT_REF:
                await BOT_REF.send_message(u["user_id"], f"📢 *Announcement*\n\n{msg}", parse_mode="Markdown")
                sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📢 Broadcast sent: ✅{sent} ❌{failed}", reply_markup=admin_keyboard())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# F. Airdrop
# ═══════════════════════════════════════════════════════════════
async def adm_airdrop_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "airdrop"):
        return await query.answer("⛔ Only owners & managers can airdrop")
    await query.answer()
    await query.edit_message_text("🪂 Send the amount to airdrop to ALL active users:")
    return WAIT_AIRDROP_AMOUNT


async def adm_airdrop_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "airdrop"):
        await update.message.reply_text("⛔ Only owners & managers can airdrop.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    try:
        amount = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid number.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    db = get_db()
    result = await db["users"].update_many(
        {"banned": False},
        {"$inc": {"balance": amount, "total_mined": amount}}
    )
    await update.message.reply_text(
        f"🪂 Airdrop sent! *{result.modified_count}* users received *{amount}* coins each.",
        parse_mode="Markdown", reply_markup=admin_keyboard()
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# G. Settings
# ═══════════════════════════════════════════════════════════════
async def adm_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, color_state: bool = None):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    s = DEFAULT_SETTINGS
    brand = s.get("brand_name", "Lessa") or "Lessa"
    wm = s.get("watermark_text", "") or "(none)"
    wallets = s.get("payment_wallets", {})
    wl = " | ".join(f"{c}: {'✅' if wallets.get(c) else '❌'}" for c in list(wallets)[:4])
    text = (
        f"⚙️ *Bot Settings*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Min Withdrawal: *{s['min_withdrawal']}*\n"
        f"💸 Max Daily Withdrawal: *{s['max_withdrawal_daily']}*\n"
        f"📊 Withdrawal Fee: *{s['withdrawal_fee_percent']}%*\n"
        f"⏰ Processing Time: *{s['withdrawal_processing_minutes']} min*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Daily Claim Base: *{s['daily_claim_base']}*\n"
        f"🔥 Max Streak Bonus: *{s['max_daily_streak_bonus']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 Boost 2x: *{s['boost2x_price']}* | 5x: *{s['boost5x_price']}*\n"
        f"👥 Referral: *{s['referral_bonus_percent']}%* (mining *{s.get('referral_mining_bonus_percent', 0)}%*)\n"
        f"🏊 Pool: bonus *{s['pool_bonus_percent']}%* | fee *{s['pool_fee_percent']}%*\n"
        f"⏰ Halving: *{s.get('halving_date') or 'Not set'}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Wallets: {wl or '(none set)'}\n"
        f"🏷️ Brand Name: *{brand}* (shown as '{brand} Core' etc. on proofs)\n"
        f"💧 Watermark: _{wm}_\n"
    )
    ap = "🟢 ON" if s.get("auto_proof_enabled") else "🔴 OFF"
    buttons = [
        [InlineKeyboardButton("💰 Min Withdrawal", callback_data="adm_set_minwd"),
         InlineKeyboardButton("💸 Max Daily", callback_data="adm_edit_max_withdrawal_daily")],
        [InlineKeyboardButton("📊 Withdrawal Fee %", callback_data="adm_edit_withdrawal_fee_percent"),
         InlineKeyboardButton("⏰ Process Time", callback_data="adm_edit_withdrawal_processing_minutes")],
        [InlineKeyboardButton("🎁 Daily Claim", callback_data="adm_set_daily"),
         InlineKeyboardButton("🔥 Max Streak", callback_data="adm_edit_max_daily_streak_bonus")],
        [InlineKeyboardButton("🚀 Boost Prices", callback_data="adm_set_boost"),
         InlineKeyboardButton("👥 Referral %", callback_data="adm_set_referral")],
        [InlineKeyboardButton("🏊 Pool %", callback_data="adm_edit_pool_bonus_percent"),
         InlineKeyboardButton("⏰ Halving Date", callback_data="adm_set_halving")],
        [InlineKeyboardButton("💳 Payment Wallets", callback_data="adm_wallets"),
         InlineKeyboardButton("🏷️ Brand Name", callback_data="adm_edit_brand_name")],
        [InlineKeyboardButton("💧 Watermark", callback_data="adm_edit_watermark")],
        [InlineKeyboardButton("⛏️ Mining", callback_data="adm_mining"),
         InlineKeyboardButton(f"📨 Auto-Proofs: {ap}", callback_data="adm_ap_toggle")],
        [InlineKeyboardButton(f"⏱️ Interval: {s.get('auto_proof_interval_minutes', 60)}m", callback_data="adm_ap_interval"),
         InlineKeyboardButton("📤 Send Proof Now", callback_data="adm_ap_send_now")],
        [InlineKeyboardButton("🎨 Colored Buttons: " + ("🟢 ON" if s.get("button_colors", True) else "🔴 OFF"), callback_data="adm_btncolor_toggle"),
         InlineKeyboardButton("🖌️ Customize Buttons", callback_data="adm_btncolor_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ]
    if color_state is not None:
        text += f"\n{_btncolor_note(buttons, color_state, 'this settings page')}\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View / edit payment wallet addresses for every coin."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    wallets = DEFAULT_SETTINGS.get("payment_wallets", {})
    from config.constants import SUPPORTED_CRYPTOS
    text = "💳 *Payment Wallets*\n\n"
    for c in SUPPORTED_CRYPTOS:
        addr = wallets.get(c) or "Not set"
        short = addr if len(addr) <= 14 else addr[:8] + "..." + addr[-6:]
        text += f"{c}: `{short}`\n"
    text += "\nTap a coin to change its receiving address. Used for plan payments."
    buttons = []
    row = []
    for c in SUPPORTED_CRYPTOS:
        row.append(InlineKeyboardButton(c, callback_data=f"adm_edit_wallet_{c}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Back to Settings", callback_data="adm_settings")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generic entry: adm_edit_<setting_key> or adm_edit_wallet_<COIN> or adm_edit_watermark."""
    query = update.callback_query
    await query.answer()
    data = query.data.replace("adm_edit_", "")
    if data.startswith("wallet_"):
        coin = data.replace("wallet_", "")
        current = DEFAULT_SETTINGS.get("payment_wallets", {}).get(coin, "Not set")
        await query.edit_message_text(
            f"💳 Wallet for *{coin}*\n\nCurrent: `{current}`\n\nSend the new {coin} address:",
            parse_mode="Markdown",
        )
        context.user_data["setting_key"] = data  # "wallet_<COIN>"
    elif data == "watermark":
        current = DEFAULT_SETTINGS.get("watermark_text", "") or "(none)"
        await query.edit_message_text(
            f"💧 *Watermark*\n\nCurrent: _{current}_\n\nSend the new watermark text, or `-` to remove it.",
            parse_mode="Markdown",
        )
        context.user_data["setting_key"] = "watermark_text"
    else:
        current = DEFAULT_SETTINGS.get(data, "")
        label = data.replace("_", " ").title()
        await query.edit_message_text(
            f"⚙️ *{label}*\n\nCurrent: *{current}*\n\nSend new value:",
            parse_mode="Markdown",
        )
        context.user_data["setting_key"] = data
    return WAIT_SETTINGS_VALUE


async def adm_set_minwd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(f"💰 Current min withdrawal: {DEFAULT_SETTINGS['min_withdrawal']}\n\nSend new value:")
    context.user_data["setting_key"] = "min_withdrawal"
    return WAIT_SETTINGS_VALUE


async def adm_set_daily_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(f"🎁 Current daily claim base: {DEFAULT_SETTINGS['daily_claim_base']}\n\nSend new value:")
    context.user_data["setting_key"] = "daily_claim_base"
    return WAIT_SETTINGS_VALUE


async def adm_set_boost_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"🚀 Boost 2x: {DEFAULT_SETTINGS['boost2x_price']} | 5x: {DEFAULT_SETTINGS['boost5x_price']}\n\n"
        f"Send: `<2x_price> <5x_price>`",
        parse_mode="Markdown"
    )
    context.user_data["setting_key"] = "boosts"
    return WAIT_SETTINGS_VALUE


async def adm_set_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(f"👥 Current referral bonus: {DEFAULT_SETTINGS['referral_bonus_percent']}%\n\nSend new percentage:")
    context.user_data["setting_key"] = "referral_bonus_percent"
    return WAIT_SETTINGS_VALUE


async def adm_set_halving_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = DEFAULT_SETTINGS.get("halving_date", "Not set")
    await query.edit_message_text(
        f"⏰ Current halving date: {current}\n\nSend new date in YYYY-MM-DD format:\n(e.g. 2026-12-25)",
    )
    context.user_data["setting_key"] = "halving_date"
    return WAIT_SETTINGS_VALUE


async def adm_set_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("setting_key", "")
    if key.startswith("wallet_"):
        if not can(update.effective_user.id, "manage_wallets"):
            await update.message.reply_text("⛔ Only owners can edit wallet addresses.", reply_markup=admin_keyboard())
            return ConversationHandler.END
    elif not can(update.effective_user.id, "manage_settings"):
        await update.message.reply_text("⛔ Only owners & managers can change settings.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    val = update.message.text.strip()
    kb = admin_keyboard()

    if key == "brand_name":
        if val.lower() in ("-", "none", "remove", "lessa"):
            DEFAULT_SETTINGS["brand_name"] = "Lessa"
            await update.message.reply_text("✅ Brand name reset to *Lessa*.", parse_mode="Markdown", reply_markup=kb)
        else:
            DEFAULT_SETTINGS["brand_name"] = val
            await update.message.reply_text(
                f"✅ Brand name set to *{val}* — proofs now show '{val} Core', '{val} Swift' etc.",
                parse_mode="Markdown", reply_markup=kb
            )
    elif key == "watermark_text":
        if val.lower() in ("-", "none", "remove"):
            DEFAULT_SETTINGS["watermark_text"] = ""
            await update.message.reply_text("✅ Watermark removed.", reply_markup=kb)
        else:
            DEFAULT_SETTINGS["watermark_text"] = val
            await update.message.reply_text(f"✅ Watermark set to: _{val}_", parse_mode="Markdown", reply_markup=kb)
    elif key.startswith("wallet_"):
        coin = key.replace("wallet_", "")
        DEFAULT_SETTINGS.setdefault("payment_wallets", {})[coin] = val
        save_settings()
        await update.message.reply_text(f"✅ {coin} wallet updated.", reply_markup=kb)
    elif key == "boosts":
        parts = val.split()
        if len(parts) == 2:
            DEFAULT_SETTINGS["boost2x_price"] = int(parts[0])
            DEFAULT_SETTINGS["boost5x_price"] = int(parts[1])
            await update.message.reply_text(f"✅ Boost prices: 2x={parts[0]}, 5x={parts[1]}", reply_markup=kb)
        else:
            await update.message.reply_text("❌ Send two numbers: `<2x_price> <5x_price>`", parse_mode="Markdown", reply_markup=kb)
    elif key == "halving_date":
        # Accept YYYY-MM-DD format
        try:
            from datetime import datetime as dt
            dt_obj = dt.fromisoformat(val)
            DEFAULT_SETTINGS["halving_date"] = dt_obj.isoformat()
            await update.message.reply_text(f"✅ Halving date set to: {dt_obj.strftime('%B %d, %Y')}", reply_markup=kb)
        except ValueError:
            await update.message.reply_text("❌ Invalid date. Use YYYY-MM-DD format.", reply_markup=kb)
    elif key == "referral_bonus_percent":
        try:
            v = int(val)
            DEFAULT_SETTINGS["referral_bonus_percent"] = v
            DEFAULT_SETTINGS["referral_mining_bonus_percent"] = v // 2
            await update.message.reply_text(f"✅ Referral bonus: {v}% (mining: {v//2}%)", reply_markup=kb)
        except ValueError:
            await update.message.reply_text("❌ Invalid number.", reply_markup=kb)
    elif key in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[key], (int, float)):
        try:
            new_val = int(val) if isinstance(DEFAULT_SETTINGS[key], int) else float(val)
            DEFAULT_SETTINGS[key] = new_val
            await update.message.reply_text(f"✅ {key} set to {new_val}", reply_markup=kb)
        except ValueError:
            await update.message.reply_text("❌ Invalid number.", reply_markup=kb)
    elif key in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[key], str):
        # Generic string setting (e.g. maintenance_message, brand_name...)
        DEFAULT_SETTINGS[key] = val
        await update.message.reply_text(f"✅ {key} updated.", reply_markup=kb)
    else:
        await update.message.reply_text("❌ Unknown setting.", reply_markup=kb)
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# H. Coin Prices
# ═══════════════════════════════════════════════════════════════
async def adm_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coin prices view — shows LIVE market price with a 🔵 badge when we have
    it, plus the manual value used as fallback when offline."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    text = "🪙 *Coin Prices (USD)* — 🔵 LIVE = real market\n\n"
    buttons = []
    for coin in COIN_PRICES:
        info = COIN_INFO.get(coin, {})
        price = market_price(coin)
        live = bool(live_price(coin))
        badge = " 🔵 LIVE" if live else ""
        text += f"{info.get('emoji', '🪙')} *{coin}*: ${price:,.4f} | Mult: {COIN_MULTIPLIERS.get(coin, 1)}x{badge}\n"
        buttons.append([InlineKeyboardButton(f"Edit {coin}", callback_data=f"adm_price_{coin}")])
    buttons.append([InlineKeyboardButton("🔄 Refresh Live", callback_data="adm_prices_refresh")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_prices_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force a live price refresh and re-render the prices view."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer("🔄 Refreshing live prices…")
    await refresh_live_prices(force=True)
    await adm_prices(update, context)


async def adm_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    coin = query.data.split("_")[-1]
    context.user_data["edit_coin"] = coin
    await query.answer()
    await query.edit_message_text(f"🪙 Current {coin} price: ${COIN_PRICES.get(coin, 0)}\n\nSend new USD price:")
    return WAIT_COIN_PRICE


async def adm_price_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "manage_settings"):
        await update.message.reply_text("⛔ Only owners & managers can change prices.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    coin = context.user_data.get("edit_coin", "USDT")
    try:
        price = float(update.message.text.strip())
        COIN_PRICES[coin] = price
        DEFAULT_SETTINGS["coin_prices"] = dict(COIN_PRICES)  # persist (auto-saves)
        await update.message.reply_text(f"✅ {coin} price set to ${price:,.2f}", reply_markup=admin_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Invalid number.", reply_markup=admin_keyboard())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# I. Force Join Channels
# ═══════════════════════════════════════════════════════════════
async def adm_forcejoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    channels = DEFAULT_SETTINGS.get("force_join_channels", [])
    enabled = DEFAULT_SETTINGS.get("force_join_enabled", False)
    text = (
        f"📢 *Force Join Settings*\n\n"
        f"Status: {'🟢 ON' if enabled else '🔴 OFF'}\n\n"
        f"*Required Channels:*\n"
    )
    if channels:
        for ch in channels:
            text += f"• {entry_label(ch)}\n"
    else:
        text += "_(None configured — proof groups are used instead)_\n"
    buttons = [
        [InlineKeyboardButton(f"{'🔴 Disable' if enabled else '🟢 Enable'}", callback_data="adm_fj_toggle")],
        [InlineKeyboardButton("➕ Add Channel", callback_data="adm_fj_add")],
        [InlineKeyboardButton("➖ Remove Channel", callback_data="adm_fj_remove")],
    ]
    # Per-entry actions: rename to whatever the users should see (fixes ugly
    # raw IDs like "channel -1004416191377"), and attach an ID to link-only entries.
    for i, ch in enumerate(channels):
        if entry_ref(ch) is None:
            buttons.append([InlineKeyboardButton(
                f"🔒 Add ID: {entry_label(ch)}", callback_data=f"adm_fj_linkid_{i}")])
        buttons.append([InlineKeyboardButton(
            f"✏️ Rename: {entry_label(ch)}", callback_data=f"adm_fj_editname_{i}")])
    buttons.append([InlineKeyboardButton("📝 Edit Screen Text", callback_data="adm_fj_text")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_fj_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_groups"):
        return await query.answer("⛔ Only owners & managers can manage force join")
    DEFAULT_SETTINGS["force_join_enabled"] = not DEFAULT_SETTINGS.get("force_join_enabled", False)
    await query.answer(f"Force Join: {'ON' if DEFAULT_SETTINGS['force_join_enabled'] else 'OFF'}")
    await adm_forcejoin(update, context)


async def adm_fj_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ *Add required channel*\n\n"
        "Send the **display name** users will see, then the **invite link**:\n"
        "`Withdrawal Channel | https://t.me/+abcDEFghiJk`\n\n"
        "The invite hash alone also works — no need to paste the full link:\n"
        "`Withdrawal Channel | +abcDEFghiJk`\n\n"
        "If you also have the channel's numeric ID/username, add it for automatic "
        "membership checking (best):\n"
        "`Withdrawal Channel | https://t.me/+abcDEFghiJk | -1001234567890`\n\n"
        "Public channel: `@channelname` also works alone.\n\n"
        "⚠️ Link-only (no ID): users see the name you set and the Join button "
        "opens the invite — but the bot cannot verify who actually joined, so it "
        "trusts the 'I've Joined' tap. Add the ID (bot as admin) for a hard check.",
        parse_mode="Markdown",
    )
    context.user_data["fj_action"] = "add"
    return WAIT_SETTINGS_VALUE


async def adm_fj_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    channels = DEFAULT_SETTINGS.get("force_join_channels", [])
    if not channels:
        return await query.answer("No channels to remove")
    buttons = [[InlineKeyboardButton(entry_label(ch), callback_data=f"adm_fj_del_{i}")] for i, ch in enumerate(channels)]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_forcejoin")])
    await query.answer()
    await query.edit_message_text("➖ Select channel to remove:", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_fj_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_groups"):
        return await query.answer("⛔ Only owners & managers can manage force join")
    try:
        idx = int(query.data.replace("adm_fj_del_", ""))
    except ValueError:
        return await query.answer("Invalid selection")
    channels = DEFAULT_SETTINGS.get("force_join_channels", [])
    removed = False
    if 0 <= idx < len(channels):
        channels.pop(idx)
        removed = True
        save_settings()
    await query.answer("Removed" if removed else "Not found")
    await adm_forcejoin(update, context)


async def adm_fj_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a force-join channel: Name | invite link (ID optional for auto-verify)."""
    return await _adm_add_entry(
        update, context,
        store_key="force_join_channels",
        fallback_name="Required channel",
        kind_noun="channel",
        note="It is now required before anyone can use the bot.",
    )


# ═══════════════════════════════════════════════════════════════
# J. Proof Groups
# ═══════════════════════════════════════════════════════════════
async def adm_proofgroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    groups = DEFAULT_SETTINGS.get("proof_groups", [])
    text = (
        f"📨 *Withdrawal Proof Groups*\n\n"
        f"Groups that receive automatic withdrawal proofs:\n\n"
    )
    if groups:
        for g in groups:
            text += f"• {entry_label(g)}\n"
    else:
        text += "_(No groups configured — this group also becomes the force-join requirement)_\n"
    buttons = [
        [InlineKeyboardButton("➕ Add Group", callback_data="adm_pg_add")],
        [InlineKeyboardButton("➖ Remove Group", callback_data="adm_pg_remove")],
    ]
    # Per-entry actions: rename what users see, and attach an ID to link-only entries.
    for i, g in enumerate(groups):
        if entry_ref(g) is None:
            buttons.append([InlineKeyboardButton(
                f"🔒 Add ID: {entry_label(g)}", callback_data=f"adm_pg_linkid_{i}")])
        buttons.append([InlineKeyboardButton(
            f"✏️ Rename: {entry_label(g)}", callback_data=f"adm_pg_editname_{i}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_pg_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ *Add proof / payment group*\n\n"
        "Send the **display name** users will see, then the **invite link**:\n"
        "`My Payment Group | https://t.me/+abcDEFghiJk`\n\n"
        "The invite hash alone also works — no need to paste the full link:\n"
        "`My Payment Group | +abcDEFghiJk`\n\n"
        "Add the numeric ID/username too for automatic membership checks (best):\n"
        "`My Payment Group | https://t.me/+abcDEFghiJk | -1001234567890`\n\n"
        "Public group: `@groupname` also works alone.\n\n"
        "Withdrawal proofs are sent here automatically — and when no separate "
        "force-join channels exist, users must join this same group to use the bot.\n"
        "⚠️ Link-only (no ID): join can't be verified server-side; the bot trusts "
        "the 'I've Joined' tap. Add the ID (bot as admin) for a hard check.",
        parse_mode="Markdown",
    )
    context.user_data["pg_action"] = "add"
    return WAIT_SETTINGS_VALUE


async def adm_pg_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    groups = DEFAULT_SETTINGS.get("proof_groups", [])
    if not groups:
        return await query.answer("No groups to remove")
    buttons = [[InlineKeyboardButton(entry_label(g), callback_data=f"adm_pg_del_{i}")] for i, g in enumerate(groups)]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_proofgroups")])
    await query.answer()
    await query.edit_message_text("➖ Select group to remove:", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_pg_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_groups"):
        return await query.answer("⛔ Only owners & managers can manage groups")
    try:
        idx = int(query.data.replace("adm_pg_del_", ""))
    except ValueError:
        return await query.answer("Invalid selection")
    groups = DEFAULT_SETTINGS.get("proof_groups", [])
    removed = False
    if 0 <= idx < len(groups):
        groups.pop(idx)
        removed = True
        save_settings()
    await query.answer("Removed" if removed else "Not found")
    await adm_proofgroups(update, context)


async def adm_pg_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a proof/payment group: Name | invite link (ID optional for auto-verify)."""
    return await _adm_add_entry(
        update, context,
        store_key="proof_groups",
        fallback_name="Payment group",
        kind_noun="group",
        note="Withdrawal proofs are posted here — and when no separate force-join "
             "channels are set, users must join this group before using the bot.",
    )


# ── Auto-resolve a private chat's ID for a link-only entry ──────────
# Telegram never lets a bot map an invite link to a chat id, so the bot
# can't verify membership for link-only entries. Two workarounds, both
# supported here: the admin forwards ANY message from that chat (the bot
# reads forward_from_chat.id), or sends the numeric ID/@username.
async def _adm_linkid_start(update, context, store_key, index_key, state, noun):
    query = update.callback_query
    if not can(query.from_user.id, "manage_groups"):
        return await query.answer("⛔ Only owners & managers can manage groups")
    try:
        idx = int(query.data.rsplit("_", 1)[-1])
    except ValueError:
        return await query.answer("Invalid entry")
    store = DEFAULT_SETTINGS.get(store_key, [])
    if not (0 <= idx < len(store)):
        return await query.answer("Entry not found")
    await query.answer()
    context.user_data[index_key] = idx
    await query.edit_message_text(
        f"🔒 *Attach the chat ID* (so membership is verified automatically)\n\n"
        f"Target: *{entry_label(store[idx])}*\n\n"
        f"Do one of these:\n"
        f"• 📎 Forward any message from that {noun} into this chat "
        f"(the ID is filled in automatically)\n"
        f"• Send its `-100xxxxxxxxxx` ID or `@username`",
        parse_mode="Markdown",
    )
    return state


async def _adm_linkid_value(update, context, store_key, index_key, noun):
    if not can(update.effective_user.id, "manage_groups"):
        await update.message.reply_text("⛔ Only owners & managers can manage groups.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    idx = context.user_data.get(index_key)
    store = DEFAULT_SETTINGS.get(store_key, [])
    if idx is None or not (0 <= idx < len(store)):
        await update.message.reply_text("❌ That entry no longer exists.", reply_markup=admin_keyboard())
        return ConversationHandler.END

    resolved = None
    chat_title = None
    msg = update.message
    fwd = getattr(msg, "forward_from_chat", None)
    if fwd is None:
        origin = getattr(msg, "forward_origin", None)
        if origin is not None:
            fwd = getattr(origin, "chat", None)
    if fwd is not None and getattr(fwd, "id", None) is not None:
        resolved = str(fwd.id)
        chat_title = getattr(fwd, "title", None)
    else:
        val = (msg.text or msg.caption or "").strip()
        ref = _parse_chat_ref(val)
        if ref is None:
            await update.message.reply_text(
                f"❌ Couldn't read a chat from that. Forward a message from the {noun}, "
                f"or send its `-100xxxx` ID / `@username`.",
                parse_mode="Markdown", reply_markup=admin_keyboard())
            return ConversationHandler.END
        resolved = str(ref) if isinstance(ref, int) else ref

    # The bot must be able to see that chat, otherwise the hard gate would
    # trap every user in a check that can never pass.
    try:
        member = await context.bot.get_chat_member(resolved, context.bot.id)
        ok = member.status in ("administrator", "creator", "member")
    except Exception:
        ok = False
    if not ok:
        await update.message.reply_text(
            f"⚠️ The bot can't see `{resolved}` — add the bot as an admin/member in "
            f"that {noun} first, then retry. Until then the entry stays tap-confirmed.",
            parse_mode="Markdown", reply_markup=admin_keyboard())
        return ConversationHandler.END

    entry = store[idx]
    if isinstance(entry, dict):
        entry["ref"] = resolved
        if chat_title and not entry.get("name"):
            entry["name"] = chat_title
    else:
        store[idx] = {"ref": resolved}
        if chat_title:
            store[idx]["name"] = chat_title
    save_settings()
    await update.message.reply_text(
        f"✅ *{entry_label(store[idx])}* is now **auto-verified** — membership is "
        f"checked server-side on every message (no more tap-confirm).",
        parse_mode="Markdown", reply_markup=admin_keyboard())
    return ConversationHandler.END


async def adm_fj_linkid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_linkid_start(update, context, "force_join_channels", "fj_link_index", WAIT_FJ_ID, "channel")


async def adm_fj_linkid_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_linkid_value(update, context, "force_join_channels", "fj_link_index", "channel")


async def adm_pg_linkid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_linkid_start(update, context, "proof_groups", "pg_link_index", WAIT_PG_ID, "group")


async def adm_pg_linkid_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_linkid_value(update, context, "proof_groups", "pg_link_index", "group")


# ── Rename what users see for a force-join / proof entry ───────────
# Lets the admin replace ugly raw IDs ("channel -1004416191377") with any
# friendly name ("Withdrawal Channel") — the join screen and buttons then
# show the name, while the real id/link stays untouched.
async def _adm_editname_start(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              store_key, index_key, state, noun):
    query = update.callback_query
    if not can(query.from_user.id, "manage_groups"):
        return await query.answer("⛔ Only owners & managers can manage groups")
    try:
        idx = int(query.data.rsplit("_", 1)[-1])
    except ValueError:
        return await query.answer("Invalid entry")
    store = DEFAULT_SETTINGS.get(store_key, [])
    if not (0 <= idx < len(store)):
        return await query.answer("Entry not found")
    await query.answer()
    context.user_data[index_key] = idx
    await query.edit_message_text(
        f"✏️ *Rename {noun}*\n\n"
        f"Current: `{entry_label(store[idx])}`\n\n"
        f"Send the new name users will see — e.g. `Withdrawal Channel`\n"
        f"(the real ID / invite link stays untouched).",
        parse_mode="Markdown",
    )
    return state


async def _adm_editname_value(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              store_key, index_key, view_fn):
    if not can(update.effective_user.id, "manage_groups"):
        await update.message.reply_text("⛔ Only owners & managers can manage groups.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    idx = context.user_data.get(index_key)
    store = DEFAULT_SETTINGS.get(store_key, [])
    if idx is None or not (0 <= idx < len(store)):
        await update.message.reply_text("❌ That entry no longer exists.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("❌ Name can't be empty.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    entry = store[idx]
    if isinstance(entry, dict):
        entry["name"] = name
    else:
        store[idx] = {"ref": entry, "name": name}
    save_settings()
    await update.message.reply_text(
        f"✅ Renamed to *{name}* — users will see this on the join screen.",
        parse_mode="Markdown", reply_markup=admin_keyboard())
    return ConversationHandler.END


async def adm_fj_editname_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_editname_start(update, context, "force_join_channels", "fj_name_index", WAIT_FJ_NAME, "channel")


async def adm_fj_editname_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_editname_value(update, context, "force_join_channels", "fj_name_index", adm_forcejoin)


async def adm_pg_editname_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_editname_start(update, context, "proof_groups", "pg_name_index", WAIT_PG_NAME, "group")


async def adm_pg_editname_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _adm_editname_value(update, context, "proof_groups", "pg_name_index", adm_proofgroups)


# ── Edit the force-join screen heading/subheading ───────────────────
async def adm_fj_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_groups"):
        return await query.answer("⛔ Only owners & managers can manage force join")
    await query.answer()
    cur_title = DEFAULT_SETTINGS.get("force_join_title") or "Join Required Channels"
    cur_sub = DEFAULT_SETTINGS.get("force_join_subtitle") or \
        "You must join the following channels to use this bot:"
    await query.edit_message_text(
        f"📝 *Force-Join Screen Text*\n\n"
        f"Current heading:\n`{cur_title}`\n\n"
        f"Current subheading:\n`{cur_sub}`\n\n"
        f"Send the new heading and subheading separated by `|`:\n\n"
        f"`Join To Continue | You must join our channels first`\n\n"
        f"Or send just the heading to keep the current subheading.",
        parse_mode="Markdown",
    )
    return WAIT_FJ_TEXT


async def adm_fj_text_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "manage_groups"):
        await update.message.reply_text("⛔ Only owners & managers can manage force join.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("❌ Text can't be empty.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|", 1)]
        title, subtitle = parts[0], parts[1]
    else:
        title = raw
        subtitle = DEFAULT_SETTINGS.get("force_join_subtitle") or \
            "You must join the following channels to use this bot:"
    DEFAULT_SETTINGS["force_join_title"] = title
    DEFAULT_SETTINGS["force_join_subtitle"] = subtitle
    save_settings()
    await update.message.reply_text(
        f"✅ Force-join screen updated.\n\n"
        f"📢 *{title}*\n{subtitle}",
        parse_mode="Markdown", reply_markup=admin_keyboard())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# K. Mining Config
# ═══════════════════════════════════════════════════════════════
async def adm_mining(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    s = DEFAULT_SETTINGS
    text = (
        f"📈 *Mining Configuration*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Interval: *{s['mining_interval_seconds']}s*\n"
        f"💰 Reward Min: *{s['mining_reward_min']}*\n"
        f"💰 Reward Max: *{s['mining_reward_max']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Bonus: *{s['bonus_block_chance']*100}%*\n"
        f"🦄 Super: *{s['super_bonus_chance']*100}%*\n"
        f"🌟 Mega: *{s['mega_bonus_chance']*100}%*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    buttons = [
        [InlineKeyboardButton("⏱️ Interval", callback_data="adm_mine_interval"),
         InlineKeyboardButton("💰 Rewards", callback_data="adm_mine_rewards")],
        [InlineKeyboardButton("🎁 Bonuses", callback_data="adm_mine_bonuses")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_mine_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(f"⏱️ Current interval: {DEFAULT_SETTINGS['mining_interval_seconds']}s\n\nSend new interval in seconds:")
    context.user_data["setting_key"] = "mining_interval_seconds"
    return WAIT_SETTINGS_VALUE


async def adm_mine_rewards_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"💰 Current: min={DEFAULT_SETTINGS['mining_reward_min']} max={DEFAULT_SETTINGS['mining_reward_max']}\n\n"
        f"Send: `<min> <max>`", parse_mode="Markdown"
    )
    context.user_data["setting_key"] = "mining_rewards"
    return WAIT_SETTINGS_VALUE


async def adm_mine_bonuses_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = DEFAULT_SETTINGS
    await query.edit_message_text(
        f"🎁 Bonus: {s['bonus_block_chance']*100}% | Super: {s['super_bonus_chance']*100}% | Mega: {s['mega_bonus_chance']*100}%\n\n"
        f"Send: `<bonus%> <super%> <mega%>`", parse_mode="Markdown"
    )
    context.user_data["setting_key"] = "mining_bonuses"
    return WAIT_SETTINGS_VALUE


async def adm_mining_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not can(update.effective_user.id, "manage_settings"):
        await update.message.reply_text("⛔ Only owners & managers can change mining config.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    key = context.user_data.get("setting_key", "")
    val = update.message.text.strip()

    if key == "mining_rewards":
        parts = val.split()
        if len(parts) == 2:
            DEFAULT_SETTINGS["mining_reward_min"] = float(parts[0])
            DEFAULT_SETTINGS["mining_reward_max"] = float(parts[1])
            await update.message.reply_text(f"✅ Rewards: min={parts[0]} max={parts[1]}", reply_markup=admin_keyboard())
    elif key == "mining_bonuses":
        parts = val.split()
        if len(parts) == 3:
            DEFAULT_SETTINGS["bonus_block_chance"] = float(parts[0]) / 100
            DEFAULT_SETTINGS["super_bonus_chance"] = float(parts[1]) / 100
            DEFAULT_SETTINGS["mega_bonus_chance"] = float(parts[2]) / 100
            await update.message.reply_text(f"✅ Bonuses updated", reply_markup=admin_keyboard())
    elif key in DEFAULT_SETTINGS:
        try:
            new_val = int(val) if isinstance(DEFAULT_SETTINGS[key], int) else float(val)
            DEFAULT_SETTINGS[key] = new_val
            await update.message.reply_text(f"✅ {key} = {new_val}", reply_markup=admin_keyboard())
        except ValueError:
            await update.message.reply_text("❌ Invalid number.", reply_markup=admin_keyboard())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# L. Maintenance
# ═══════════════════════════════════════════════════════════════
async def adm_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    enabled = os.getenv("ENABLE_MAINTENANCE") == "true"
    text = f"🔒 *Maintenance Mode*: {'🟢 ON' if enabled else '🔴 OFF'}"
    buttons = [
        [InlineKeyboardButton(f"{'🔴 Turn OFF' if enabled else '🟢 Turn ON'}", callback_data="adm_maint_toggle")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def adm_maint_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_settings"):
        return await query.answer("⛔ Only owners & managers can toggle maintenance")
    current = os.getenv("ENABLE_MAINTENANCE") == "true"
    os.environ["ENABLE_MAINTENANCE"] = "false" if current else "true"
    await query.answer(f"Maintenance: {'OFF' if current else 'ON'}")
    await adm_maintenance(update, context)


# ═══════════════════════════════════════════════════════════════
# M. Pools
# ═══════════════════════════════════════════════════════════════
async def adm_pools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    await query.answer()
    pools = await Pool.get_active_pools()
    text = "🏊 *Mining Pools*\n\n"
    for p in pools:
        text += f"• *{p['name']}* ({p['type']}) — {len(p.get('members', []))} members, fee {p.get('fee', 5)}%\n"
    if not pools:
        text += "No pools configured."
    buttons = [[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


# ═══════════════════════════════════════════════════════════════
# Forward withdrawal proof to groups
# ═══════════════════════════════════════════════════════════════
from config.constants import generate_fake_tx, generate_proof_id


async def forward_withdrawal_proof(withdrawal):
    """Forward a realistic withdrawal proof message to configured groups."""
    import logging
    logger = logging.getLogger(__name__)

    groups = DEFAULT_SETTINGS.get("proof_groups", [])
    if not groups:
        logger.info("No proof groups configured, skipping proof send")
        return
    if not BOT_REF:
        logger.warning("BOT_REF not set, cannot send proofs")
        return

    amount = withdrawal.amount
    coin = withdrawal.coin
    plan = withdrawal.plan or "Free"
    uid = _gen_uid()  # Alphanumeric UID for this user

    await refresh_live_prices()  # proof shows the real current price
    text = _build_proof_text(uid, plan, coin)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Start Mining", url=BOT_LINK)]])

    sent = 0
    for group_entry in groups:
        gid = entry_ref(group_entry)
        if gid is None:
            logger.warning(f"Withdrawal proof skipped: no chat id for {group_entry} "
                           f"(add the group's ID via 🔒 Add ID so proofs can be sent)")
            continue
        try:
            await BOT_REF.send_message(gid, text, parse_mode="Markdown", reply_markup=kb)
            sent += 1
            logger.info(f"Proof sent to {gid}")
        except Exception as e:
            logger.error(f"Failed to send proof to {group_entry}: {e}")
    logger.info(f"Withdrawal proofs sent: {sent}/{len(groups)}")


# ═══════════════════════════════════════════════════════════════
# Helper: parse chat/channel reference from user input
# Accepts: numeric ID, @username, or https://t.me/username links
# ═══════════════════════════════════════════════════════════════
def _parse_chat_ref(val: str):
    import re
    val = val.strip()
    # https://t.me/username or https://t.me/+link or t.me/username
    m = re.match(r"(?:https?://)?t\.me/(\+?\w+)", val)
    if m:
        return "@" + m.group(1) if not m.group(1).startswith("+") else val
    # @username
    if val.startswith("@"):
        return val
    # Numeric ID (negative for groups, positive for users)
    try:
        num = int(val)
        return num
    except ValueError:
        return None


def _norm_invite(s):
    """Normalize a join/invite link to https://t.me/... form."""
    s = (s or "").strip()
    if s.startswith("https://t.me/"):
        return s
    if s.startswith("t.me/"):
        return "https://" + s
    if s.startswith("+"):
        return "https://t.me/" + s
    return s


def _parse_entry_line(raw):
    """Parse an admin 'add channel/group' line into {name, ref, link}.

    Field order is flexible; supported shapes:
      My Group | https://t.me/+inviteLink
      My Group | https://t.me/+inviteLink | -1001234567890
      -1001234567890 https://t.me/+inviteLink        (legacy)
      @groupname
      https://t.me/groupname | My Group
    Returns None when nothing usable (no link and no ref) is present.
    """
    import re as _re
    if not raw or not raw.strip():
        return None
    # '|' / newline separates name, link and id — otherwise fall back to spaces
    if "|" in raw or "\n" in raw:
        tokens = [t.strip() for t in _re.split(r"[|\n]+", raw) if t.strip()]
    else:
        tokens = [t.strip() for t in raw.split() if t.strip()]
    name_parts, ref, link = [], None, None
    for tok in tokens:
        low = tok.lower()
        if low.startswith(("https://t.me/", "http://t.me/", "t.me/")):
            norm = _norm_invite(tok)
            if norm.startswith("https://t.me/"):
                link = norm
                path = norm.split("t.me/", 1)[-1] if "t.me/" in norm else ""
                # A public t.me/name link is also a usable chat ref
                if ref is None and path and not path.startswith("+") and not path.startswith("c/") and "/" not in path:
                    ref = "@" + path.split("?")[0]
        elif tok.startswith("@") or tok.lstrip("-").isdigit():
            ref = tok
        elif tok.startswith("+"):
            # Bare invite hash, e.g. +iRM6QuEggH8wYThk → https://t.me/+iRM6QuEggH8wYThk
            norm = _norm_invite(tok)
            if norm.startswith("https://t.me/+"):
                link = norm
        else:
            name_parts.append(tok)
    if not link and not ref:
        return None
    return {"name": " ".join(name_parts).strip() or None, "ref": ref, "link": link}


def _entries_equal(a, b):
    """True when two stored entries refer to the same chat.
    Matches by ref, by stored invite link, or (for link-only entries) by name."""
    ra, rb = entry_ref(a), entry_ref(b)
    if ra is not None or rb is not None:
        return str(ra) == str(rb)
    if isinstance(a, dict) and isinstance(b, dict):
        la, lb = a.get("link"), b.get("link")
        if la and lb and la == lb:
            return True
        na, nb = a.get("name"), b.get("name")
        if na and nb and na == nb:
            return True
    return False


async def _adm_add_entry(update, context, store_key, fallback_name, kind_noun, note=""):
    """Shared handler for adding a force-join channel / proof group.

    Accepts 'Display Name | invite link' and optionally appends the chat's
    numeric ID or @username for real membership verification:
        My Group | https://t.me/+invite | -1001234567890
    Entries without a ref (link-only) can't be verified server-side, so the
    bot trusts the user's 'I've Joined' tap for those.
    """
    if not can(update.effective_user.id, "manage_groups"):
        await update.message.reply_text(
            f"⛔ Only owners & managers can manage {kind_noun}s.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    info = _parse_entry_line(update.message.text)
    if info is None:
        await update.message.reply_text(
            "❌ Invalid input. Examples:\n"
            "• `My Group | https://t.me/+inviteLink`\n"
            "• `My Group | https://t.me/+inviteLink | -1001234567890`\n"
            "• Public: `@groupname`",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
        )
        return ConversationHandler.END
    ref, link, name = info["ref"], info["link"], info["name"]

    if ref:
        chat_ref = _parse_chat_ref(ref)
        if chat_ref is None:
            await update.message.reply_text(
                f"❌ Could not understand `{ref}` as a chat reference.", parse_mode="Markdown", reply_markup=admin_keyboard())
            return ConversationHandler.END
        await update.message.reply_text(f"⏳ Verifying `{ref}`...", parse_mode="Markdown")
        try:
            chat = await context.bot.get_chat(chat_ref)
            bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
            if bot_member.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    f"⚠️ Bot is not admin in `{chat.title}`. Make the bot an admin first — "
                    f"or drop the ID and send only the name + invite link "
                    f"(then join is tap-confirmed, not verified).",
                    parse_mode="Markdown", reply_markup=admin_keyboard())
                return ConversationHandler.END
            entry = await _make_chat_entry(context.bot, chat, link)
            if name:
                if isinstance(entry, dict):
                    entry["name"] = name
                else:
                    entry = {"ref": entry, "name": name}
        except Exception as e:
            await update.message.reply_text(
                f"❌ Could not find {kind_noun} `{chat_ref}`.\n\nMake sure:\n"
                f"• The bot is added as admin\n• The link/username is correct\n"
                f"• Error: `{str(e)[:100]}`",
                parse_mode="Markdown", reply_markup=admin_keyboard())
            return ConversationHandler.END
    else:
        entry = {"name": name or fallback_name, "link": link}

    store = DEFAULT_SETTINGS.setdefault(store_key, [])
    replaced = False
    for i, old in enumerate(list(store)):
        if _entries_equal(old, entry):
            store[i] = entry
            replaced = True
            break
    if not replaced:
        store.append(entry)
    save_settings()

    show_name = entry["name"] if isinstance(entry, dict) else entry_label(entry)
    verified = entry_ref(entry) is not None
    verify_txt = (
        "🔒 Membership: *auto-verified* (server-side check)"
        if verified else
        "⚠️ Membership: *tap-confirmed* (link-only — Telegram won't let the bot "
        "verify who joined a private chat from its invite link alone)"
    )
    extra = f"\n\n{note}" if note else ""
    await update.message.reply_text(
        f"✅ *Saved!*\n\nName users see: *{show_name}*\n{verify_txt}\n"
        f"Join link: `{entry_link(entry)}`{extra}",
        parse_mode="Markdown",
        reply_markup=admin_keyboard(),
    )
    return ConversationHandler.END


async def _make_chat_entry(bot, chat, link_token=None):
    """
    Build the stored entry for a chat:
      public  -> "@username"
      private -> {"ref": chat.id, "link": invite} if an invite link is available
                 (provided by the admin, or auto-exported by the bot)
      private with no link -> plain chat.id (Join button will be manual)
    """
    if getattr(chat, "username", None):
        return "@" + chat.username
    link = _norm_invite(link_token) if link_token else None
    if not (link and "t.me/" in link):
        link = None
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            link = None
    if link:
        entry = {"ref": chat.id, "link": link}
        if getattr(chat, "title", None):
            entry["name"] = chat.title
        return entry
    return chat.id


# ═══════════════════════════════════════════════════════════════
# Conversation cancel handler — breaks out of any active conv
# ═══════════════════════════════════════════════════════════════
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If admin clicks any callback while in a conversation, end it and show admin panel."""
    query = update.callback_query
    if query:
        await query.answer()
    await admin_back(update, context)
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# RANDOM AUTO-PROOF SCHEDULER
# ═══════════════════════════════════════════════════════════════
AUTO_PROOF_JOB_NAME = "auto_proof_chain"


def _arm_next_proof(context):
    """Schedule the next auto-proof at a random interval (10-45 minutes)."""
    import random as _rnd
    if context is None or getattr(context, "job_queue", None) is None:
        return
    next_delay = _rnd.randint(10, 45) * 60  # seconds
    context.job_queue.run_once(schedule_next_proof, when=next_delay, name=AUTO_PROOF_JOB_NAME)


async def schedule_next_proof(context):
    """Send a proof, then schedule the next one at a random interval.

    The chain is re-armed FIRST and proof generation is wrapped in
    try/except so no failure (network, API, bad group entry) can ever
    kill the loop — the #1 cause of auto-proofs silently stopping after
    a few days.
    """
    import logging
    logger = logging.getLogger(__name__)
    _arm_next_proof(context)
    if not DEFAULT_SETTINGS.get("auto_proof_enabled", True):
        return
    try:
        await auto_generate_proof(context)
    except Exception as e:
        logger.error(f"Auto-proof generation failed (chain continues): {e}", exc_info=True)


async def proof_watchdog(context):
    """Re-arm the auto-proof chain if it ever died (host restart, crash,
    job dropped for any reason). Runs hourly."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        jobs = context.job_queue.jobs()
        if any(getattr(j, "name", None) == AUTO_PROOF_JOB_NAME for j in jobs):
            return
        _arm_next_proof(context)
        logger.warning("⚠️ Auto-proof chain was dead — watchdog re-armed it")
    except Exception as e:
        logger.warning(f"Auto-proof watchdog check failed: {e}")


# ═══════════════════════════════════════════════════════════════
# AUTO-PROOF CONTROLS — toggle, interval, send now
# ═══════════════════════════════════════════════════════════════
async def adm_ap_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_settings"):
        return await query.answer("⛔ Only owners & managers can change that")
    DEFAULT_SETTINGS["auto_proof_enabled"] = not DEFAULT_SETTINGS.get("auto_proof_enabled", True)
    status = "🟢 ON" if DEFAULT_SETTINGS["auto_proof_enabled"] else "🔴 OFF"
    await query.answer(f"Auto-proofs: {status}")
    await adm_settings(update, context)


async def adm_btncolor_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle painted buttons from the Settings page (shows counts too)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    DEFAULT_SETTINGS["button_colors"] = not DEFAULT_SETTINGS.get("button_colors", True)
    enabled = DEFAULT_SETTINGS["button_colors"]
    await query.answer(f"🎨 Button colors: {'ON' if enabled else 'OFF'}")
    await adm_settings(update, context, color_state=enabled)


async def adm_btncolor_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle painted buttons straight from the main admin panel row."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Access denied")
    DEFAULT_SETTINGS["button_colors"] = not DEFAULT_SETTINGS.get("button_colors", True)
    enabled = DEFAULT_SETTINGS["button_colors"]
    kb = admin_keyboard(query.from_user.id)
    note = _btncolor_note(kb.inline_keyboard, enabled, "this panel")
    await query.answer(f"🎨 Button colors: {'ON' if enabled else 'OFF'}")
    await query.edit_message_text(
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "    👑 *ADMIN CONTROL PANEL*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{note}\n\n"
        f"Tap the 🎨 BUTTON COLORS row below to switch back.\n",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def adm_ap_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = DEFAULT_SETTINGS.get("auto_proof_interval_minutes", 60)
    await query.edit_message_text(
        f"⏱️ Current auto-proof interval: *{current} minutes*\n\n"
        f"Send new interval in minutes:\n"
        f"`(e.g. 30 for every 30 min, 60 for every hour)`",
        parse_mode="Markdown",
    )
    context.user_data["setting_key"] = "auto_proof_interval_minutes"
    return WAIT_SETTINGS_VALUE


async def adm_ap_send_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can(query.from_user.id, "manage_settings"):
        return await query.answer("⛔ Only owners & managers can do that")
    await query.answer("📤 Sending proof...")
    await auto_generate_proof()
    await query.answer("✅ Proof sent!")
    await adm_settings(update, context)


async def adm_test_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a test proof directly to the admin's chat."""
    query = update.callback_query
    if not can(query.from_user.id, "send_test_proof"):
        return await query.answer("⛔ Only owners & managers can send proofs")
    await query.answer("📤 Sending test proof...")
    # Build a test proof and send it right here
    uid = _gen_uid()
    import random as _rnd
    from config.constants import SUPPORTED_CRYPTOS
    coin = _rnd.choice(SUPPORTED_CRYPTOS)
    amount = round(_rnd.uniform(50, 500), 2)
    plans = ["Starter", "Pro", "Pro", "Elite", "VIP"]
    plan = _rnd.choice(plans)
    text = _build_proof_text(uid, plan, coin, amount)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Start Mining", url=BOT_LINK)]])
    await context.bot.send_message(query.from_user.id, text, parse_mode="Markdown", reply_markup=kb)
    await query.answer("✅ Test proof sent to your chat!")


# ═══════════════════════════════════════════════════════════════
# AUTO-PROOF GENERATOR — sends fake proofs on a schedule
# ═══════════════════════════════════════════════════════════════
def _gen_uid():
    """Generate a random alphanumeric UID like fcznxn94z1ep."""
    import random as _rnd, string
    chars = string.ascii_lowercase + string.digits
    return "".join(_rnd.choices(chars, k=12))


def _coin_usd(coin, amount):
    """Convert coin amount to approximate USD (live price when available)."""
    return round(amount * market_price(coin), 2)


# Proof button → the bot itself (from .env BOT_USERNAME)
BOT_LINK = "https://t.me/" + os.getenv("BOT_USERNAME", "Lessa_mining_bot").lstrip("@")

# Plan model name suffixes, e.g. brand "Lessa" → "Lessa Core"
PLAN_MODEL_SUFFIX = {
    "Free": "Core",
    "Starter": "Swift",
    "Pro": "Pro",
    "Elite": "Elite",
    "VIP": "Vault",
}


def _model_name(plan):
    """Full model name using the admin-editable brand name (default 'Lessa')."""
    brand = DEFAULT_SETTINGS.get("brand_name", "Lessa") or "Lessa"
    return f"{brand} {PLAN_MODEL_SUFFIX.get(plan, 'Core')}"


# Realistic amount ranges per plan (in USD)
PLAN_RANGES = {
    "Free": (5, 50),
    "Starter": (20, 200),
    "Pro": (50, 500),
    "Elite": (100, 2000),
    "VIP": (200, 5000),
}


def _build_proof_text(uid, plan, coin, amount=None):
    """Build a proof message — amount is derived from the LIVE market price
    of the coin (falls back to the admin-set price when offline)."""
    import random as _rnd
    usd_range = PLAN_RANGES.get(plan, (10, 100))
    usd = round(_rnd.uniform(*usd_range), 2)
    coin_price = market_price(coin)
    if amount is None:
        amount = round(usd / coin_price, 6) if coin_price > 0 else usd
    live = bool(live_price(coin))
    price_line = (
        f"\n💹 {coin} Price: *${coin_price:,.4f}* 🔵 LIVE"
        if live else "\n💹 {coin} Price: *${coin_price:,.4f}*".format(coin=coin, coin_price=coin_price)
    )
    model = _model_name(plan)
    text = (
        f"📊 *NEW PROFIT RECORDED*\n\n"
        f"💰 Amount: *{amount} {coin}* ≈ *${usd:,.2f}*\n"
        f"🆔 UID: `{uid}`\n"
        f"📦 Plan: *{plan}*\n"
        f"🤖 Model: *{model}*{price_line}"
    )
    wm = DEFAULT_SETTINGS.get("watermark_text", "")
    if wm:
        text += f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n💧 _{wm}_"
    return text


async def auto_generate_proof(context=None):
    """Generate a random fake withdrawal proof and send to all proof groups."""
    import logging, random as _rnd
    from config.constants import SUPPORTED_CRYPTOS

    logger = logging.getLogger(__name__)
    groups = DEFAULT_SETTINGS.get("proof_groups", [])
    if not groups:
        return
    bot = BOT_REF or (context.bot if context else None)
    if not bot:
        return

    await refresh_live_prices()  # amounts correlate with the real market
    # Pick random details — weighted toward lower plans (more realistic)
    coin = _rnd.choice(SUPPORTED_CRYPTOS)
    uid = _gen_uid()
    plans = ["Free", "Free", "Free", "Starter", "Starter", "Pro", "Pro", "Elite", "VIP"]
    plan = _rnd.choice(plans)

    text = _build_proof_text(uid, plan, coin)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Start Mining", url=BOT_LINK)]])

    # Amount for the log line (same math as _build_proof_text)
    usd_range = PLAN_RANGES.get(plan, (10, 100))
    usd = round(_rnd.uniform(*usd_range), 2)
    coin_price = market_price(coin)
    amount = round(usd / coin_price, 6) if coin_price > 0 else usd

    sent = 0
    for group_entry in groups:
        gid = entry_ref(group_entry)
        if gid is None:
            logger.warning(f"Auto-proof skipped: no chat id for {group_entry} "
                           f"(add the group's ID via 🔒 Add ID so proofs can be sent)")
            continue
        try:
            await bot.send_message(gid, text, parse_mode="Markdown", reply_markup=kb)
            sent += 1
        except Exception as e:
            logger.error(f"Auto-proof failed for {group_entry}: {e}")
    logger.info(f"Auto-proof: {sent}/{len(groups)} groups sent ({amount} {coin} uid={uid})")


# ═══════════════════════════════════════════════════════════════
# Setup admin conversation handlers
# ═══════════════════════════════════════════════════════════════
def setup_admin(app: Application):
    global BOT_REF
    BOT_REF = app.bot

    # Conversation for user search
    user_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_user_search_start, pattern="^adm_user_search$")],
        states={WAIT_USER_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_user_search_result)]},
        fallbacks=[],
    )

    # Conversation for settings values
    settings_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(adm_set_minwd_start, pattern="^adm_set_minwd$"),
            CallbackQueryHandler(adm_set_daily_start, pattern="^adm_set_daily$"),
            CallbackQueryHandler(adm_set_boost_start, pattern="^adm_set_boost$"),
            CallbackQueryHandler(adm_set_referral_start, pattern="^adm_set_referral$"),
            CallbackQueryHandler(adm_set_halving_start, pattern="^adm_set_halving$"),
            CallbackQueryHandler(adm_ap_interval_start, pattern="^adm_ap_interval$"),
            CallbackQueryHandler(adm_mine_interval_start, pattern="^adm_mine_interval$"),
            CallbackQueryHandler(adm_mine_rewards_start, pattern="^adm_mine_rewards$"),
            CallbackQueryHandler(adm_mine_bonuses_start, pattern="^adm_mine_bonuses$"),
            CallbackQueryHandler(adm_edit_start, pattern="^adm_edit_"),
        ],
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_value)]},
        fallbacks=[],
        allow_reentry=True,
    )

    # Conversation for coin price
    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_price_start, pattern=r"^adm_price_\w+$")],
        states={WAIT_COIN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_price_set)]},
        fallbacks=[],
    )

    # Conversation for force join add
    fj_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(adm_fj_add_start, pattern="^adm_fj_add$"),
        ],
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_fj_value)]},
        fallbacks=[],
    )

    # Conversation for proof groups add
    pg_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(adm_pg_add_start, pattern="^adm_pg_add$"),
        ],
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_pg_value)]},
        fallbacks=[],
    )

    # Conversation: attach a chat ID to a link-only force-join channel
    fjid_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_fj_linkid_start, pattern=r"^adm_fj_linkid_\d+$")],
        states={WAIT_FJ_ID: [MessageHandler(filters.FORWARDED | filters.TEXT & ~filters.COMMAND, adm_fj_linkid_value)]},
        fallbacks=[],
    )

    # Conversation: attach a chat ID to a link-only proof group
    pgid_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_pg_linkid_start, pattern=r"^adm_pg_linkid_\d+$")],
        states={WAIT_PG_ID: [MessageHandler(filters.FORWARDED | filters.TEXT & ~filters.COMMAND, adm_pg_linkid_value)]},
        fallbacks=[],
    )

    # Conversation: rename a force-join channel (friendly name on the join screen)
    fjname_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_fj_editname_start, pattern=r"^adm_fj_editname_\d+$")],
        states={WAIT_FJ_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_fj_editname_value)]},
        fallbacks=[],
    )

    # Conversation: rename a proof group
    pgname_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_pg_editname_start, pattern=r"^adm_pg_editname_\d+$")],
        states={WAIT_PG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_pg_editname_value)]},
        fallbacks=[],
    )

    # Conversation: edit the force-join screen heading/subheading
    fjtext_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_fj_text_start, pattern="^adm_fj_text$")],
        states={WAIT_FJ_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_fj_text_value)]},
        fallbacks=[],
    )

    # Conversation for staff add
    staff_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_staff_add_start, pattern="^adm_staff_add$")],
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_staff_value)]},
        fallbacks=[],
    )

    # Conversation for broadcast
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_broadcast_start, pattern="^adm_broadcast$")],
        states={WAIT_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_broadcast_send)]},
        fallbacks=[],
    )

    # Conversation for airdrop
    airdrop_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_airdrop_start, pattern="^adm_airdrop$")],
        states={WAIT_AIRDROP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_airdrop_send)]},
        fallbacks=[],
    )

    # Conversation for balance edit
    bal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_bal_start, pattern=r"^adm_bal_\d+$")],
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_bal_set)]},
        fallbacks=[],
    )

    # Conversation for hash edit
    hash_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_hash_start, pattern=r"^adm_hash_\d+$")],
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_hash_set)]},
        fallbacks=[],
    )

    # Conversation for payment rejection reason
    reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_pay_reject, pattern="^adm_pay_reject_")],
        states={
            WAIT_REJECT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adm_pay_reject_reason),
                CommandHandler("skip", adm_pay_reject_reason),
            ]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^adm_payments$")],
    )

    # Conversation for mining rewards/bonuses set
    mining_set_conv = ConversationHandler(
        entry_points=[],  # triggered through adm_mining_set which checks setting_key
        states={WAIT_SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_mining_set)]},
        fallbacks=[],
    )

    app.add_handler(user_search_conv)
    app.add_handler(settings_conv)
    app.add_handler(price_conv)
    app.add_handler(fj_conv)
    app.add_handler(fjid_conv)
    app.add_handler(pg_conv)
    app.add_handler(pgid_conv)
    app.add_handler(fjname_conv)
    app.add_handler(pgname_conv)
    app.add_handler(fjtext_conv)
    app.add_handler(staff_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(airdrop_conv)
    app.add_handler(bal_conv)
    app.add_handler(hash_conv)
    app.add_handler(mining_set_conv)

    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(adm_font_toggle, pattern="^adm_font_toggle$"))
    app.add_handler(CallbackQueryHandler(adm_font_menu, pattern="^adm_font_menu$"))
    app.add_handler(CallbackQueryHandler(adm_style_set, pattern=r"^adm_style_(smallcaps|title|plain)$"))
    app.add_handler(CallbackQueryHandler(adm_close, pattern="^adm_close$"))
    app.add_handler(CallbackQueryHandler(adm_staff, pattern="^adm_staff$"))
    app.add_handler(CallbackQueryHandler(adm_staff_remove_start, pattern="^adm_staff_remove$"))
    app.add_handler(CallbackQueryHandler(adm_staff_del, pattern=r"^adm_staff_del_"))
    app.add_handler(CallbackQueryHandler(adm_dashboard, pattern="^adm_dashboard$"))
    app.add_handler(CallbackQueryHandler(adm_users, pattern="^adm_users$"))
    app.add_handler(CallbackQueryHandler(adm_users_next, pattern="^adm_users_next$"))
    app.add_handler(CallbackQueryHandler(adm_users_prev, pattern="^adm_users_prev$"))
    app.add_handler(CallbackQueryHandler(adm_user_detail, pattern=r"^adm_user_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_plan, pattern=r"^adm_plan_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_plan_set, pattern=r"^adm_planset_"))
    app.add_handler(CallbackQueryHandler(adm_ban, pattern=r"^adm_ban_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_reset, pattern=r"^adm_reset_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_withdrawals, pattern="^adm_withdrawals$"))
    app.add_handler(CallbackQueryHandler(adm_wd_detail, pattern=r"^adm_wd_(?!approve|reject)"))
    app.add_handler(CallbackQueryHandler(adm_wd_approve, pattern=r"^adm_wd_approve_"))
    app.add_handler(CallbackQueryHandler(adm_wd_reject, pattern=r"^adm_wd_reject_"))
    app.add_handler(CallbackQueryHandler(adm_payments, pattern="^adm_payments$"))
    app.add_handler(CallbackQueryHandler(adm_pay_detail, pattern=r"^adm_pay_(?!verify|reject)"))
    app.add_handler(CallbackQueryHandler(adm_pay_verify, pattern=r"^adm_pay_verify_"))
    app.add_handler(reject_conv)
    app.add_handler(CallbackQueryHandler(adm_settings, pattern="^adm_settings$"))
    app.add_handler(CallbackQueryHandler(adm_wallets, pattern="^adm_wallets$"))
    app.add_handler(CallbackQueryHandler(adm_prices, pattern="^adm_prices$"))
    app.add_handler(CallbackQueryHandler(adm_prices_refresh, pattern="^adm_prices_refresh$"))
    app.add_handler(CallbackQueryHandler(adm_forcejoin, pattern="^adm_forcejoin$"))
    app.add_handler(CallbackQueryHandler(adm_fj_toggle, pattern="^adm_fj_toggle$"))
    app.add_handler(CallbackQueryHandler(adm_fj_remove_start, pattern="^adm_fj_remove$"))
    app.add_handler(CallbackQueryHandler(adm_fj_del, pattern=r"^adm_fj_del_"))
    app.add_handler(CallbackQueryHandler(adm_proofgroups, pattern="^adm_proofgroups$"))
    app.add_handler(CallbackQueryHandler(adm_pg_remove_start, pattern="^adm_pg_remove$"))
    app.add_handler(CallbackQueryHandler(adm_pg_del, pattern=r"^adm_pg_del_"))
    app.add_handler(CallbackQueryHandler(adm_mining, pattern="^adm_mining$"))
    app.add_handler(CallbackQueryHandler(adm_maintenance, pattern="^adm_maintenance$"))
    app.add_handler(CallbackQueryHandler(adm_maint_toggle, pattern="^adm_maint_toggle$"))
    app.add_handler(CallbackQueryHandler(adm_pools, pattern="^adm_pools$"))
    app.add_handler(CallbackQueryHandler(adm_ap_toggle, pattern="^adm_ap_toggle$"))
    app.add_handler(CallbackQueryHandler(adm_btncolor_toggle, pattern="^adm_btncolor_toggle$"))
    app.add_handler(CallbackQueryHandler(adm_btncolor_panel, pattern="^adm_btncolor_panel$"))
    app.add_handler(CallbackQueryHandler(adm_btncolor_menu, pattern="^adm_btncolor_menu$"))
    app.add_handler(CallbackQueryHandler(adm_bclr_reset, pattern="^adm_bclr_reset$"))
    app.add_handler(CallbackQueryHandler(adm_bclr_tap, pattern="^adm_bclr_"))
    app.add_handler(CallbackQueryHandler(adm_ap_send_now, pattern="^adm_ap_send_now$"))
    app.add_handler(CallbackQueryHandler(adm_test_proof, pattern="^adm_test_proof$"))
