"""
CryptoMinerPro — Constants and Configuration
USDT is the default crypto. All settings are editable from admin panel.
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)  # .env always wins over inherited env vars

# ─── Upgrade Plans ───
UPGRADE_PLANS = {
    "Free": {
        "name": "Free",
        "price": "0",
        "coin": "N/A",
        "multiplier": 1,
        "hash_rate": 1,
        "daily_bonus": 0.5,
        "max_boosts": 0,
        "pool_access": ["public"],
        "features": ["Basic mining", "1x hash rate", "Daily claim"],
        "icon": "🆓",
        "sort_order": 0,
    },
    "Starter": {
        "name": "Starter",
        "price": "20",
        "coin": "USDT",
        "multiplier": 1.5,
        "hash_rate": 5,
        "daily_bonus": 2,
        "max_boosts": 1,
        "pool_access": ["public"],
        "features": ["1.5x mining speed", "5 H/s hash rate", "1 active boost", "Priority queue"],
        "icon": "⭐",
        "sort_order": 1,
    },
    "Pro": {
        "name": "Pro",
        "price": "50",
        "coin": "USDT",
        "multiplier": 2.5,
        "hash_rate": 25,
        "daily_bonus": 5,
        "max_boosts": 2,
        "pool_access": ["public", "vip"],
        "features": ["2.5x mining speed", "25 H/s hash rate", "2 active boosts", "VIP pool access", "Auto-mining"],
        "icon": "💎",
        "sort_order": 2,
    },
    "Elite": {
        "name": "Elite",
        "price": "150",
        "coin": "USDT",
        "multiplier": 5,
        "hash_rate": 100,
        "daily_bonus": 15,
        "max_boosts": 3,
        "pool_access": ["public", "vip", "elite"],
        "features": ["5x mining speed", "100 H/s hash rate", "3 active boosts", "All pool access", "Auto-mining + priority", "Exclusive achievements"],
        "icon": "👑",
        "sort_order": 3,
    },
    "VIP": {
        "name": "VIP",
        "price": "500",
        "coin": "USDT",
        "multiplier": 10,
        "hash_rate": 500,
        "daily_bonus": 50,
        "max_boosts": 5,
        "pool_access": ["public", "vip", "elite"],
        "features": ["10x mining speed", "500 H/s hash rate", "5 active boosts", "All pool access", "Auto-mining + priority", "Exclusive achievements", "Custom badge", "Direct admin support"],
        "icon": "🚀",
        "sort_order": 4,
    },
}

PLAN_ORDER = ["Free", "Starter", "Pro", "Elite", "VIP"]

# ─── Supported Cryptos ───
SUPPORTED_CRYPTOS = ["USDT", "BTC", "ETH", "TRX", "TON", "SOL", "LTC", "DOGE"]

# ─── Coin Multipliers (affects reward calculation) ───
COIN_MULTIPLIERS = {
    "USDT": 1.0,
    "BTC": 0.8,
    "ETH": 0.9,
    "TRX": 1.1,
    "TON": 1.05,
    "LTC": 1.2,
    "SOL": 1.1,
    "DOGE": 1.3,
}

# ─── Coin Prices (USD — simulated) ───
COIN_PRICES = {
    "USDT": 1.0,
    "BTC": 65000,
    "ETH": 3500,
    "TRX": 0.12,
    "TON": 5.50,
    "LTC": 80,
    "SOL": 180,
    "DOGE": 0.15,
}

# ─── Coin Info ───
COIN_INFO = {
    "USDT": {"name": "Tether", "symbol": "₮", "emoji": "🟢", "decimals": 6},
    "BTC": {"name": "Bitcoin", "symbol": "₿", "emoji": "🟠", "decimals": 8},
    "ETH": {"name": "Ethereum", "symbol": "Ξ", "emoji": "🔷", "decimals": 6},
    "TRX": {"name": "Tron", "symbol": "TRX", "emoji": "🔴", "decimals": 6},
    "TON": {"name": "Toncoin", "symbol": "TON", "emoji": "🔵", "decimals": 9},
    "LTC": {"name": "Litecoin", "symbol": "Ł", "emoji": "⚪", "decimals": 6},
    "SOL": {"name": "Solana", "symbol": "◎", "emoji": "💜", "decimals": 4},
    "DOGE": {"name": "Dogecoin", "symbol": "Ð", "emoji": "🐕", "decimals": 8},
}

# ─── Wallet Addresses ───
def _load_wallets() -> dict:
    """Load wallet addresses from .env at startup (editable later via admin panel)."""
    wallets = {}
    for c in SUPPORTED_CRYPTOS:
        key = c.replace(" ", "_").upper()
        addr = os.getenv(f"WALLET_{key}") or os.getenv(f"REAL_{key}_ADDRESS")
        if addr and addr != "N/A":
            wallets[c] = addr
    return wallets


def get_wallet(coin: str) -> str:
    """Wallet address — admin-edited value first, then .env."""
    key = coin.replace(" ", "_").upper()
    wallets = DEFAULT_SETTINGS.get("payment_wallets", {})
    addr = wallets.get(coin) or wallets.get(key)
    if addr:
        return addr
    addr = os.getenv(f"WALLET_{key}")
    if addr:
        return addr
    return os.getenv(f"REAL_{key}_ADDRESS", "N/A")


def market_price(coin: str) -> float:
    """Best USD price for a coin: live market price when available, else the
    admin-set static COIN_PRICES value. Never raises, never returns <= 0."""
    try:
        from config import live_prices as _lp
        v = _lp.live_price(coin)
        if v:
            return float(v)
    except Exception:
        pass
    key = str(coin or "").upper()
    try:
        p = COIN_PRICES.get(key) or COIN_PRICES.get(coin) or 1
        return float(p) if p and p > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0

# ─── Achievements ───
ACHIEVEMENTS_LIST = [
    {"id": "first_mine", "name": "First Strike", "description": "Complete your first mining session", "icon": "⛏️", "requirement": {"type": "mining_sessions", "count": 1}, "reward": 10},
    {"id": "mining_10", "name": "Getting Started", "description": "Complete 10 mining sessions", "icon": "🔨", "requirement": {"type": "mining_sessions", "count": 10}, "reward": 50},
    {"id": "mining_50", "name": "Seasoned Miner", "description": "Complete 50 mining sessions", "icon": "⛏️", "requirement": {"type": "mining_sessions", "count": 50}, "reward": 200},
    {"id": "mining_100", "name": "Mining Veteran", "description": "Complete 100 mining sessions", "icon": "🏆", "requirement": {"type": "mining_sessions", "count": 100}, "reward": 500},
    {"id": "balance_100", "name": "Triple Digits", "description": "Reach a balance of 100 coins", "icon": "💰", "requirement": {"type": "balance", "count": 100}, "reward": 25},
    {"id": "balance_1000", "name": "Four Digits", "description": "Reach a balance of 1,000 coins", "icon": "💵", "requirement": {"type": "balance", "count": 1000}, "reward": 100},
    {"id": "balance_10000", "name": "Five Digits", "description": "Reach a balance of 10,000 coins", "icon": "🤑", "requirement": {"type": "balance", "count": 10000}, "reward": 500},
    {"id": "total_mined_500", "name": "Steady Earner", "description": "Mine a total of 500 coins", "icon": "📈", "requirement": {"type": "total_mined", "count": 500}, "reward": 75},
    {"id": "total_mined_5000", "name": "Gold Digger", "description": "Mine a total of 5,000 coins", "icon": "🥇", "requirement": {"type": "total_mined", "count": 5000}, "reward": 300},
    {"id": "total_mined_50000", "name": "Unstoppable", "description": "Mine a total of 50,000 coins", "icon": "💪", "requirement": {"type": "total_mined", "count": 50000}, "reward": 2500},
    {"id": "referral_1", "name": "First Recruit", "description": "Refer your first friend", "icon": "🤝", "requirement": {"type": "referrals", "count": 1}, "reward": 20},
    {"id": "referral_5", "name": "Team Builder", "description": "Refer 5 friends", "icon": "👥", "requirement": {"type": "referrals", "count": 5}, "reward": 100},
    {"id": "referral_10", "name": "Ambassador", "description": "Refer 10 friends", "icon": "🌍", "requirement": {"type": "referrals", "count": 10}, "reward": 300},
    {"id": "streak_7", "name": "Week Warrior", "description": "Claim daily bonus 7 days in a row", "icon": "🔥", "requirement": {"type": "daily_streak", "count": 7}, "reward": 75},
    {"id": "streak_30", "name": "Monthly Master", "description": "Claim daily bonus 30 days in a row", "icon": "📅", "requirement": {"type": "daily_streak", "count": 30}, "reward": 500},
    {"id": "plan_starter", "name": "Going Places", "description": "Upgrade to Starter plan", "icon": "⭐", "requirement": {"type": "plan", "plan": "Starter"}, "reward": 50},
    {"id": "plan_pro", "name": "Professional", "description": "Upgrade to Pro plan", "icon": "💎", "requirement": {"type": "plan", "plan": "Pro"}, "reward": 200},
    {"id": "plan_elite", "name": "Elite Status", "description": "Upgrade to Elite plan", "icon": "👑", "requirement": {"type": "plan", "plan": "Elite"}, "reward": 1000},
    {"id": "plan_vip", "name": "VIP Treatment", "description": "Upgrade to VIP plan", "icon": "🚀", "requirement": {"type": "plan", "plan": "VIP"}, "reward": 5000},
    {"id": "speed_miner", "name": "Speed Demon", "description": "Mine at 500+ H/s", "icon": "⚡", "requirement": {"type": "hash_rate", "count": 500}, "reward": 200},
    {"id": "active_7_days", "name": "Regular", "description": "Stay active for 7 days", "icon": "📆", "requirement": {"type": "active_days", "count": 7}, "reward": 50},
    {"id": "active_30_days", "name": "Committed", "description": "Stay active for 30 days", "icon": "🗓️", "requirement": {"type": "active_days", "count": 30}, "reward": 300},
]

# ─── Crypto News ───
NEWS_MESSAGES = [
    {"title": "Bitcoin Surges Past $95,000", "body": "Bitcoin has reached a new all-time high as institutional adoption accelerates. Analysts project continued growth.", "sentiment": "bullish", "coins": ["BTC"]},
    {"title": "Ethereum Staking Yields Spike", "body": "Ethereum staking rewards have jumped to 8.5% APR as network activity surges following the latest upgrade.", "sentiment": "bullish", "coins": ["ETH"]},
    {"title": "Tether Market Cap Hits New Record", "body": "USDT market capitalization surpasses $120B, reinforcing its position as the leading stablecoin.", "sentiment": "bullish", "coins": ["USDT"]},
    {"title": "Solana Processes Record Transactions", "body": "Solana network set a new record during a high-load stress test.", "sentiment": "bullish", "coins": ["SOL"]},
    {"title": "Mining Difficulty Increases", "body": "The latest difficulty adjustment makes mining harder — but rewards for active miners have been boosted to compensate.", "sentiment": "neutral", "coins": ["BTC"]},
    {"title": "Litecoin Halving Event Approaches", "body": "The Litecoin halving is approaching, historically resulting in significant price appreciation.", "sentiment": "bullish", "coins": ["LTC"]},
    {"title": "Crypto Market Cap Reaches $4 Trillion", "body": "The total cryptocurrency market capitalization has surpassed $4 trillion for the first time.", "sentiment": "bullish", "coins": ["USDT", "BTC", "ETH"]},
    {"title": "Your Mining Power Increased!", "body": "Network congestion has triggered a bonus multiplier. Active miners are earning enhanced rewards!", "sentiment": "bullish", "coins": ["USDT", "BTC", "ETH", "LTC", "SOL"]},
    {"title": "DeFi Total Value Locked Hits $200B", "body": "Decentralized finance has reached a new milestone with $200B in total value locked across all protocols.", "sentiment": "bullish", "coins": ["ETH"]},
    {"title": "Institutional Crypto Adoption Accelerates", "body": "Major financial institutions continue expanding their digital asset offerings.", "sentiment": "bullish", "coins": ["BTC", "ETH"]},
]

# ─── Settings persistence ───
# Admin-panel edits are saved to settings.json so they survive restarts.
import json as _json

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")


def save_settings():
    """Persist DEFAULT_SETTINGS to settings.json (safe to call often)."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            _json.dump(DEFAULT_SETTINGS, f, indent=2, default=str)
    except Exception:
        pass


def load_settings():
    """Restore previously saved admin edits at startup."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                saved = _json.load(f)
            for k, v in saved.items():
                DEFAULT_SETTINGS[k] = v
            # Coin prices live in COIN_PRICES; restore them too
            cp = saved.get("coin_prices")
            if isinstance(cp, dict) and cp:
                COIN_PRICES.update({k: v for k, v in cp.items() if isinstance(v, (int, float))})
    except Exception:
        pass
    finally:
        _PersistentSettings._ready = True
        # Persist the merged result so the file matches the loaded state
        save_settings()


class _PersistentSettings(dict):
    """dict that auto-saves to settings.json on direct key assignment."""
    _ready = False  # becomes True once the initial load_settings() finishes

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if _PersistentSettings._ready:
            save_settings()

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        if _PersistentSettings._ready:
            save_settings()


# ─── Default Settings (ALL editable via admin panel) ───
DEFAULT_SETTINGS = _PersistentSettings({
    "base_hash_rate": int(os.getenv("BASE_HASH_RATE", "1")),
    "mining_interval_seconds": int(os.getenv("MINING_INTERVAL_SECONDS", "3")),
    "mining_reward_min": float(os.getenv("MINING_REWARD_MIN", "0.001")),
    "mining_reward_max": float(os.getenv("MINING_REWARD_MAX", "0.05")),
    "bonus_block_chance": float(os.getenv("BONUS_BLOCK_CHANCE", "0.05")),
    "super_bonus_chance": float(os.getenv("SUPER_BONUS_CHANCE", "0.02")),
    "mega_bonus_chance": float(os.getenv("MEGA_BONUS_CHANCE", "0.01")),
    # Referrals
    "referral_bonus_percent": 10,
    "referral_mining_bonus_percent": 5,
    # Daily Claims
    "daily_claim_base": 10,
    "max_daily_streak_bonus": 50,
    # Withdrawals
    "min_withdrawal": 100,
    "max_withdrawal_daily": 10000,
    "withdrawal_processing_minutes": 15,
    "withdrawal_fee_percent": 2,
    # Boosts
    "boost2x_price": 50,
    "boost5x_price": 200,
    "boost_durations": {"1hr": 3600, "6hr": 21600, "24hr": 86400},
    # Pool
    "pool_bonus_percent": 15,
    "pool_fee_percent": 5,
    # Auto-proof generation
    "auto_proof_enabled": True,
    "auto_proof_interval_minutes": 60,  # send a proof every N minutes
    "auto_proof_min_amount": 50,
    "auto_proof_max_amount": 500,
    # Halving
    "halving_date": None,  # ISO date string, e.g. "2026-12-25T00:00:00+00:00"
    # Maintenance
    "maintenance_message": "🔧 System maintenance in progress. Please try again later.",
    # Limits
    "max_mining_history_entries": 1000,
    "max_payment_history_entries": 50,
    # Force join — configure via .env: ENABLE_FORCE_JOIN=true,
    # FORCE_JOIN_CHANNELS=@channel1,@channel2 (or t.me links / numeric ids).
    # If FORCE_JOIN_CHANNELS is empty, the proof groups (added in the admin
    # panel) are used as the required channels instead.
    "force_join_channels": [x.strip() for x in os.getenv("FORCE_JOIN_CHANNELS", "").split(",") if x.strip()],
    "force_join_enabled": os.getenv("ENABLE_FORCE_JOIN", "true").lower() in ("1", "true", "yes", "on"),
    # Force-join screen text — editable in the admin panel (no code changes)
    "force_join_title": os.getenv("FORCE_JOIN_TITLE", "Join Required Channels"),
    "force_join_subtitle": os.getenv("FORCE_JOIN_SUBTITLE",
                                     "You must join the following channels to use this bot:"),
    # Payment wallets — editable in admin panel (starts from .env values)
    "payment_wallets": _load_wallets(),
    # Brand name — replaces the "Lessa" text shown on proof messages
    "brand_name": os.getenv("BRAND_NAME", "Lessa"),
    # Watermark — extra branding line at the bottom of withdrawal/payment proofs
    "watermark_text": os.getenv("WATERMARK", ""),
    # Withdrawal proof groups
    "proof_groups": [],  # list of group chat IDs to forward withdrawal proofs
    # Staff added via /admin panel: {"<user_id>": "owner"|"manager"|"moderator"}
    # (IDs in .env ADMIN_IDS are always owners and cannot be demoted)
    "staff": {},
    # Auto chain-verification of payment TX hashes (fake-receipt detection)
    "chain_verify_enabled": True,
    # Global UI font style for user-facing messages:
    #   "smallcaps" = small-caps Unicode font, "title" = clean Title Case,
    #   "plain"    = normal text (font off)
    "ui_style": os.getenv("UI_STYLE", "smallcaps").strip().lower() or "smallcaps",
    # Real painted inline buttons (Telegram Bot API 9.4 style field):
    # green = success, red = danger, everything else = blue (primary)
    "button_colors": os.getenv("BUTTON_COLORS", "true").lower() not in ("0", "false", "no", "off"),
    # Per-button color pins from the admin picker (🖌️ Button Colors):
    # {"callback_data": "success"|"danger"|"primary"} — exact or prefix
    # keys ending in "*". Pinned buttons keep their color even when the
    # global "button_colors" toggle is off.
    "button_color_overrides": {},
    # Staff who opted to see the styled font in their own admin chat too
    "styled_admins": [],
})

# Restore any admin-panel edits saved in previous runs
load_settings()

# ─── Staff roles & permissions ───
ROLE_OWNER, ROLE_MANAGER, ROLE_MODERATOR = "owner", "manager", "moderator"

ROLE_LABELS = {
    ROLE_OWNER: "👑 Owner",
    ROLE_MANAGER: "🛠️ Manager (almost-full access)",
    ROLE_MODERATOR: "👁️ Moderator (view only)",
}

# manager = everything except editing wallets & managing staff
_STAFF_ALL = ["view", "approve_payments", "approve_withdrawals", "manage_users",
              "manage_settings", "manage_wallets", "manage_groups", "manage_staff",
              "broadcast", "airdrop", "send_test_proof"]
STAFF_PERMS = {
    ROLE_OWNER: list(_STAFF_ALL),
    ROLE_MANAGER: [p for p in _STAFF_ALL if p not in ("manage_wallets", "manage_staff")],
    ROLE_MODERATOR: ["view"],
}


def env_owner_ids() -> list:
    """Owners configured in .env ADMIN_IDS — always owners, never demotable."""
    ids = []
    for x in os.getenv("ADMIN_IDS", "").split(","):
        x = str(x).strip()
        if x.lstrip("-").isdigit():
            ids.append(int(x))
    return ids


def get_role(uid) -> str:
    """Role of a user id: saved staff role, or 'owner' for .env ADMIN_IDS."""
    staff = DEFAULT_SETTINGS.get("staff") or {}
    role = staff.get(str(uid))
    if role in STAFF_PERMS:
        return role
    if int(uid) in env_owner_ids():
        return ROLE_OWNER
    return None


def is_staff(uid) -> bool:
    return get_role(uid) is not None


def is_owner(uid) -> bool:
    return get_role(uid) == ROLE_OWNER


def can(uid, perm: str) -> bool:
    """Permission check against the user's role."""
    role = get_role(uid)
    return bool(role and perm in STAFF_PERMS.get(role, []))


def all_staff_ids() -> list:
    """Every staff member (owners + panel-added staff)."""
    ids = set(env_owner_ids())
    for k, v in (DEFAULT_SETTINGS.get("staff") or {}).items():
        if str(k).lstrip("-").isdigit() and v in STAFF_PERMS:
            ids.add(int(k))
    return sorted(ids)


# ─── "Cool font" styling — small-caps Unicode UI (ᴛʜɪꜱ ꜱᴛʏʟᴇ) ───
# Every user-facing message the bot sends is styled unless it contains
# code/links/usernames (those stay untouched so they still work).

_SC_MAP = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ғ",
    "g": "ɢ", "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ",
    "m": "ᴍ", "n": "ɴ", "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ",
    "s": "ꜱ", "t": "ᴛ", "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x",
    "y": "ʏ", "z": "ᴢ",
}
_SC_TABLE = str.maketrans(_SC_MAP)

import re as _re
# Don't touch — these must stay exactly as typed, in BOTH fonts:
#   • inline code `...`, urls (http/https/t.me), @usernames
#   • slash commands: /start, /balance@MyBot (Telegram commands are
#     case-sensitive, so users must be able to read/copy them)
#   • technical values: hash-rate units (H/s, EH/s…), hex TX hashes /
#     addresses (0x…), and long alphanumeric ids/addresses with digits
#     (BTC/TRX/DOGE addresses, txids, order ids) — even OUTSIDE `code`
_SC_PROTECT = _re.compile(
    r"(`[^`\n]*`"
    r"|https?://[^\s()<>\[\]]+"
    r"|\bt\.me/[^\s()<>\[\]]+"
    r"|@[A-Za-z0-9_]{4,}"
    r"|(?<![A-Za-z0-9_])/(?:[A-Za-z_][A-Za-z0-9_]*)(?:@[A-Za-z0-9_]+)?"
    r"|\b(?:[KMGTPE]?H/s)\b"
    r"|0x[0-9a-fA-F]{8,}"
    r"|(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{10,}"
    r")"
)


def sc_text(text):
    """Convert plain text to the small-caps 'cool font' (ᴇxᴀᴍᴘʟᴇ)."""
    if not isinstance(text, str):
        return text
    return text.translate(_SC_TABLE)


def _map_protected(text, fn):
    """Apply fn to text but keep code snippets, links and @handles intact."""
    if not isinstance(text, str):
        return text
    parts = _SC_PROTECT.split(text)
    return "".join(p if (i % 2) else fn(p) for i, p in enumerate(parts))


def styled(text):
    """Small-caps Unicode font:  ᴅᴜᴇ ᴛᴏ ɪssᴜᴇꜱ
    Code snippets, links and @handles stay intact."""
    return _map_protected(text, lambda s: s.translate(_SC_TABLE))


_TITLE_KEEP_CAPS = {
    "usdt", "usdt_trc20", "btc", "eth", "doge", "ltc", "sol", "bch", "dash",
    "vip", "otp", "sms", "tx", "id", "url", "nft", "cpu", "gpu", "ram",
    "usd", "min", "hr", "h/s",
}


def _title_word(s):
    # Capitalize the first letter of every word, keep real acronyms (USDT, VIP)
    # and inner case untouched.  "your files" → "Your Files"
    def _repl(m):
        w = m.group(0)
        if w.lower() in _TITLE_KEEP_CAPS:
            return w.upper()
        return w[0].upper() + w[1:]
    return _re.sub(r"[A-Za-z][A-Za-z0-9]*(?:'[A-Za-z]+)?", _repl, s)


def title_text(text):
    """Clean 'Title Case' style:  Your Files Will Be Deleted Within 10 Minutes."""
    return _map_protected(text, _title_word)


# ─── Painted inline buttons (Telegram Bot API 9.4 `style` field) ───
# Telegram 9.4+ lets bots paint buttons: "success" (green), "danger"
# (red), "primary" (blue). The color-chip emojis (🟢🔴🔵…) an earlier UI
# put in front of labels are converted into the real color and dropped
# from the label; buttons without a chip default to blue so the whole
# UI reads as one painted theme.
BTN_CHIP_STYLE = {
    "🟢": "success", "🔴": "danger", "🔵": "primary",
    "🟡": "primary", "🟣": "primary", "🟠": "primary",
}
BTN_DANGER_PREFIXES = ("❌", "⏹️", "🚫", "✖️", "🗑️", "⛔")
BTN_SUCCESS_PREFIXES = ("✅", "✔️", "➕", "⛏️", "⚡")


def paint_button_label(text):
    """Return (style, clean_label) for an inline button label.

    Used both to paint buttons at serialization time and to build
    color-count summaries in the admin panel.
    """
    label = str(text or "")
    chip_style = BTN_CHIP_STYLE.get(label[:1])
    if chip_style:
        return chip_style, label[1:].lstrip()
    if label.startswith(BTN_DANGER_PREFIXES):
        return "danger", label
    if label.startswith(BTN_SUCCESS_PREFIXES):
        return "success", label
    return "primary", label


# ─── Legacy "Color chips" helpers — Telegram can't color buttons, so we
# used colored-square emojis as the closest visual (green = money/actions,
# blue = info/tools, red = danger/admin/close). Kept for any remaining
# chip-style labels; new code should use the real paint_button_label().

def chip(color: str) -> str:
    """Return the emoji color chip for a semantic color name."""
    return {
        "green": "🟢", "blue": "🔵", "red": "🔴", "yellow": "🟡",
        "purple": "🟣", "orange": "🟠", "dark": "⚫",
    }.get(str(color).lower(), "🔵")


def cbtn(label: str, color: str) -> str:
    """Label with a leading color chip:  cbtn('MINE', 'green') → '🟢 MINE'"""
    return f"{chip(color)} {label}"


# ─── Helper: chat entries for force join / proof groups ───
# An entry can be:
#   * "@username" or int id            → join link derived (public)
#   * {"ref": <id/@user>, "link": ...} → private chat with an invite link

def _num_link(cid):
    cid = int(cid)
    if str(cid).startswith("-100"):
        return f"https://t.me/c/{str(cid)[4:]}"
    return f"https://t.me/c/{abs(cid)}"


def entry_ref(entry):
    """Telegram chat reference (numeric id or @username) for API calls."""
    if isinstance(entry, dict):
        return entry.get("ref") or entry.get("id")
    return entry


def entry_link(entry):
    """Clickable join link: explicit invite link first, then derived from ref."""
    if isinstance(entry, dict):
        lnk = entry.get("link")
        if lnk:
            return lnk
        ref = entry.get("ref") or entry.get("id")
    else:
        ref = entry
    if ref is None:
        return "https://t.me/"
    if isinstance(ref, str):
        ref = ref.strip()
        if ref.lstrip("-").isdigit():
            return _num_link(int(ref))
        return f"https://t.me/{ref.lstrip('@')}"
    return _num_link(ref)


def entry_label(entry):
    """Short human label for an entry (used in admin lists + join prompts)."""
    if isinstance(entry, dict):
        name = entry.get("name")
        ref = entry.get("ref") or entry.get("id")
        if name:
            return name
        return f"{ref} (private, invite saved)" if entry.get("link") else f"{ref} (private, no invite)"
    if isinstance(entry, str):
        return entry if entry.startswith("@") else f"channel {entry}"
    return f"channel {entry}"


# ─── Helper: format number ───
def fmt(n):
    if n >= 1e6:
        return f"{n/1e6:.2f}M"
    if n >= 1e3:
        return f"{n/1e3:.2f}K"
    return f"{n:.2f}"

# ─── Helper: format duration ───
def fmt_dur(s):
    if not s or s <= 0:
        return "0s"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sc = int(s % 60)
    if h > 0:
        return f"{h}h {m}m {sc}s"
    if m > 0:
        return f"{m}m {sc}s"
    return f"{sc}s"

# ─── Helper: plan icon ───
def p_icon(plan):
    return {"Free": "🆓", "Starter": "⭐", "Pro": "💎", "Elite": "👑", "VIP": "🚀"}.get(plan, "🆓")

# ─── Helper: coin emoji ───
def c_emoji(coin):
    return COIN_INFO.get(coin, {}).get("emoji", "🪙")

# ─── Helper: progress bar ───
def p_bar(pct, length=10):
    filled = round(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)

# ─── Helper: referral tier ───
def ref_tier(count):
    if count >= 100:
        return {"name": "Diamond", "emoji": "👑", "next": None, "target": 100}
    if count >= 50:
        return {"name": "Platinum", "emoji": "💎", "next": "Diamond", "target": 100}
    if count >= 25:
        return {"name": "Gold", "emoji": "🥇", "next": "Platinum", "target": 50}
    if count >= 10:
        return {"name": "Silver", "emoji": "🥈", "next": "Gold", "target": 25}
    if count >= 5:
        return {"name": "Bronze", "emoji": "🥉", "next": "Silver", "target": 10}
    return {"name": "Beginner", "emoji": "⬜", "next": "Bronze", "target": 5}

# ─── Helper: generate fake tx hash ───
def generate_fake_tx(coin):
    import random
    import string
    hex_chars = "0123456789abcdef"
    if coin == "SOL":
        return "".join(random.choices(string.ascii_letters + string.digits, k=88))
    return "0x" + "".join(random.choices(hex_chars, k=64))

# ─── Helper: generate random alphanumeric ID for proof ───
def generate_proof_id():
    import random
    import string
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=12))

# ─── Helper: calc reward ───
def calc_reward(hash_rate, coin):
    import random
    base = DEFAULT_SETTINGS["mining_reward_min"] + random.random() * (
        DEFAULT_SETTINGS["mining_reward_max"] - DEFAULT_SETTINGS["mining_reward_min"]
    )
    return base * (hash_rate / DEFAULT_SETTINGS["base_hash_rate"]) * COIN_MULTIPLIERS.get(coin, 1)
