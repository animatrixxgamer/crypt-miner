"""
User model — MongoDB via Motor async.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone
from pymongo.errors import DuplicateKeyError

from config.constants import (
    UPGRADE_PLANS, PLAN_ORDER, ACHIEVEMENTS_LIST,
    DEFAULT_SETTINGS, COIN_MULTIPLIERS,
)


class User:
    """Async helper class that wraps a MongoDB document for a user."""
    collection_name = "users"

    def __init__(self, data: dict):
        self._d = data

    # ── Properties ──
    @property
    def user_id(self):
        return self._d["user_id"]

    @property
    def username(self):
        return self._d.get("username")

    @property
    def first_name(self):
        return self._d.get("first_name", "")

    @property
    def balance(self):
        return self._d.get("balance", 0.0)

    @balance.setter
    def balance(self, v):
        self._d["balance"] = max(0.0, v)

    @property
    def plan(self):
        return self._d.get("plan", "Free")

    @plan.setter
    def plan(self, v):
        self._d["plan"] = v

    @property
    def base_hash_rate(self):
        return self._d.get("base_hash_rate", 1.0)

    @base_hash_rate.setter
    def base_hash_rate(self, v):
        self._d["base_hash_rate"] = max(1.0, v)

    @property
    def plan_multiplier(self):
        return self._d.get("plan_multiplier", 1.0)

    @plan_multiplier.setter
    def plan_multiplier(self, v):
        self._d["plan_multiplier"] = v

    @property
    def hash_rate(self):
        return self._d.get("hash_rate", 1.0)

    @hash_rate.setter
    def hash_rate(self, v):
        self._d["hash_rate"] = v

    @property
    def mining_coin(self):
        return self._d.get("mining_coin", "USDT")

    @mining_coin.setter
    def mining_coin(self, v):
        self._d["mining_coin"] = v

    @property
    def total_mined(self):
        return self._d.get("total_mined", 0.0)

    @total_mined.setter
    def total_mined(self, v):
        self._d["total_mined"] = v

    @property
    def daily_mined(self):
        return self._d.get("daily_mined", 0.0)

    @daily_mined.setter
    def daily_mined(self, v):
        self._d["daily_mined"] = v

    @property
    def weekly_mined(self):
        return self._d.get("weekly_mined", 0.0)

    @weekly_mined.setter
    def weekly_mined(self, v):
        self._d["weekly_mined"] = v

    @property
    def monthly_mined(self):
        return self._d.get("monthly_mined", 0.0)

    @monthly_mined.setter
    def monthly_mined(self, v):
        self._d["monthly_mined"] = v

    @property
    def referral_count(self):
        return self._d.get("referral_count", 0)

    @referral_count.setter
    def referral_count(self, v):
        self._d["referral_count"] = v

    @property
    def referral_earnings(self):
        return self._d.get("referral_earnings", 0.0)

    @referral_earnings.setter
    def referral_earnings(self, v):
        self._d["referral_earnings"] = v

    @property
    def referral_code(self):
        return self._d.get("referral_code", "")

    @property
    def daily_streak(self):
        return self._d.get("daily_streak", 0)

    @daily_streak.setter
    def daily_streak(self, v):
        self._d["daily_streak"] = v

    @property
    def sessions(self):
        return self._d.get("sessions", 0)

    @sessions.setter
    def sessions(self, v):
        self._d["sessions"] = v

    @property
    def total_mining_time(self):
        return self._d.get("total_mining_time", 0)

    @total_mining_time.setter
    def total_mining_time(self, v):
        self._d["total_mining_time"] = v

    @property
    def banned(self):
        return self._d.get("banned", False)

    @banned.setter
    def banned(self, v):
        self._d["banned"] = v

    @property
    def ban_reason(self):
        return self._d.get("ban_reason", None)

    @ban_reason.setter
    def ban_reason(self, v):
        self._d["ban_reason"] = v

    @property
    def boosts(self):
        return self._d.get("boosts", [])

    @property
    def achievements(self):
        return self._d.get("achievements", [])

    @achievements.setter
    def achievements(self, v):
        self._d["achievements"] = v

    @property
    def referrals(self):
        return self._d.get("referrals", [])

    @property
    def payment_history(self):
        return self._d.get("payment_history", [])

    @property
    def pending_payment_id(self):
        return self._d.get("pending_payment_id")

    @pending_payment_id.setter
    def pending_payment_id(self, v):
        self._d["pending_payment_id"] = v

    @property
    def waiting_for_screenshot(self):
        return self._d.get("waiting_for_screenshot", False)

    @waiting_for_screenshot.setter
    def waiting_for_screenshot(self, v):
        self._d["waiting_for_screenshot"] = v

    @property
    def waiting_for_tx_hash(self):
        return self._d.get("waiting_for_tx_hash", False)

    @waiting_for_tx_hash.setter
    def waiting_for_tx_hash(self, v):
        self._d["waiting_for_tx_hash"] = v

    @property
    def last_active(self):
        return self._d.get("last_active")

    @last_active.setter
    def last_active(self, v):
        self._d["last_active"] = v

    @property
    def last_daily_claim(self):
        return self._d.get("last_daily_claim")

    @last_daily_claim.setter
    def last_daily_claim(self, v):
        self._d["last_daily_claim"] = v

    @property
    def created_at(self):
        return self._d.get("created_at")

    @property
    def notifications(self):
        return self._d.get("notifications", True)

    @property
    def mining_history(self):
        return self._d.get("mining_history", [])

    def to_dict(self):
        return self._d

    # ── Computed ──
    def calculate_effective_hash_rate(self):
        rate = self.base_hash_rate * self.plan_multiplier
        if self.plan != "Free":
            now = datetime.now(timezone.utc)
            for b in self.boosts:
                if b.get("active") and b.get("expires_at"):
                    exp = b["expires_at"]
                    if isinstance(exp, str):
                        exp = datetime.fromisoformat(exp)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if now < exp:
                        multiplier = 2 if b["type"] == "2x" else 5
                        rate *= multiplier
        self.hash_rate = rate
        return rate

    def add_mining_reward(self, amount):
        if amount <= 0:
            return
        rounded = round(amount, 8)
        self.balance += rounded
        self.total_mined += rounded
        self.daily_mined += rounded
        self.weekly_mined += rounded
        self.monthly_mined += rounded
        self._d["last_mine"] = datetime.now(timezone.utc)
        hist = self._d.get("mining_history", [])
        hist.append({"date": datetime.now(timezone.utc), "amount": rounded, "hash_rate": self.hash_rate})
        max_entries = DEFAULT_SETTINGS["max_mining_history_entries"]
        if len(hist) > max_entries:
            hist = hist[-max_entries:]
        self._d["mining_history"] = hist

    async def get_rank(self):
        from models import get_db
        db = get_db()
        count = await db[User.collection_name].count_documents(
            {"total_mined": {"$gt": self.total_mined}, "banned": False}
        )
        return count + 1

    async def get_achievements_progress(self):
        unlocked_ids = {a["id"] for a in self.achievements}
        progress = []
        for ach in ACHIEVEMENTS_LIST:
            is_unlocked = ach["id"] in unlocked_ids
            current = 0
            target = 1
            req = ach["requirement"]
            if req["type"] == "mining_sessions":
                current, target = self.sessions, req["count"]
            elif req["type"] == "balance":
                current, target = self.balance, req["count"]
            elif req["type"] == "total_mined":
                current, target = self.total_mined, req["count"]
            elif req["type"] == "referrals":
                current, target = self.referral_count, req["count"]
            elif req["type"] == "daily_streak":
                current, target = self.daily_streak, req["count"]
            elif req["type"] == "plan":
                current = PLAN_ORDER.index(self.plan) if self.plan in PLAN_ORDER else 0
                target = PLAN_ORDER.index(req["plan"]) if req["plan"] in PLAN_ORDER else 0
            elif req["type"] == "hash_rate":
                current, target = self.hash_rate, req["count"]
            elif req["type"] == "active_days":
                if self.created_at:
                    created = self.created_at
                    if isinstance(created, str):
                        created = datetime.fromisoformat(created)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    current = min((datetime.now(timezone.utc) - created).days, req["count"])
                target = req["count"]
            pct = min(100, int(current / target * 100)) if target > 0 else 0
            progress.append({**ach, "unlocked": is_unlocked, "current": current, "target": target, "percent": pct})
        return progress

    # ── DB static methods ──
    @staticmethod
    async def find_one(user_id: int):
        from models import get_db
        db = get_db()
        doc = await db[User.collection_name].find_one({"user_id": user_id})
        return User(doc) if doc else None

    @staticmethod
    async def find_or_create(user_id: int, extra: dict = None):
        from models import get_db
        db = get_db()
        doc = await db[User.collection_name].find_one({"user_id": user_id})
        if doc:
            return User(doc)
        now = datetime.now(timezone.utc)
        new_doc = {
            "user_id": user_id,
            "referral_code": f"ref_{user_id}_{uuid.uuid4().hex[:8]}",
            "balance": 0.0,
            "hash_rate": 1.0,
            "base_hash_rate": 1.0,
            "plan": "Free",
            "plan_multiplier": 1.0,
            "mining_coin": "USDT",
            "total_mined": 0.0,
            "daily_mined": 0.0,
            "weekly_mined": 0.0,
            "monthly_mined": 0.0,
            "referral_count": 0,
            "referral_earnings": 0.0,
            "referrals": [],
            "boosts": [],
            "achievements": [],
            "payment_history": [],
            "mining_history": [],
            "sessions": 0,
            "total_mining_time": 0,
            "daily_streak": 0,
            "last_daily_claim": None,
            "pending_payment_id": None,
            "waiting_for_screenshot": False,
            "banned": False,
            "ban_reason": None,
            "notifications": True,
            "last_active": now,
            "last_mine": None,
            "created_at": now,
            "updated_at": now,
        }
        if extra:
            new_doc.update(extra)
        try:
            await db[User.collection_name].insert_one(new_doc)
        except DuplicateKeyError:
            # Race (two updates at once) or a leftover doc — return the existing one
            doc = await db[User.collection_name].find_one({"user_id": user_id})
            if doc:
                return User(doc)
            raise
        return User(new_doc)

    async def save(self):
        from models import get_db
        db = get_db()
        self._d["updated_at"] = datetime.now(timezone.utc)
        # Clean expired boosts
        now = datetime.now(timezone.utc)
        clean_boosts = []
        for b in self.boosts:
            exp = b.get("expires_at")
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if b.get("active") and now < exp:
                clean_boosts.append(b)
        self._d["boosts"] = clean_boosts
        await db[User.collection_name].update_one(
            {"user_id": self.user_id},
            {"$set": self._d},
            upsert=True,
        )

    @staticmethod
    async def get_leaderboard(limit=10):
        from models import get_db
        db = get_db()
        cursor = db[User.collection_name].find(
            {"banned": False},
            {"_id": 0, "user_id": 1, "username": 1, "first_name": 1, "plan": 1, "total_mined": 1, "mining_coin": 1, "balance": 1}
        ).sort("total_mined", -1).limit(limit)
        return await cursor.to_list(length=limit)

    @staticmethod
    async def count_active():
        from models import get_db
        db = get_db()
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        return await db[User.collection_name].count_documents(
            {"last_active": {"$gte": since}, "banned": False}
        )

    @staticmethod
    async def count_total():
        from models import get_db
        db = get_db()
        return await db[User.collection_name].count_documents({})

    @staticmethod
    async def get_all_active(banned=False):
        from models import get_db
        db = get_db()
        cursor = db[User.collection_name].find(
            {"banned": banned},
            {"_id": 0, "user_id": 1}
        )
        return await cursor.to_list(length=None)
