"""
PaymentRequest model — MongoDB via Motor async.
"""
from datetime import datetime, timedelta, timezone


class PaymentRequest:
    collection_name = "payment_requests"

    def __init__(self, data: dict):
        self._d = data

    @property
    def id(self):
        return self._d.get("_id")

    @property
    def user_id(self):
        return self._d.get("user_id")

    @property
    def plan(self):
        return self._d.get("plan")

    @property
    def amount(self):
        return self._d.get("amount")

    @property
    def coin(self):
        return self._d.get("coin")

    @property
    def status(self):
        return self._d.get("status", "pending")

    @status.setter
    def status(self, v):
        self._d["status"] = v

    @property
    def address(self):
        return self._d.get("address")

    @property
    def screenshot_file_id(self):
        return self._d.get("screenshot_file_id")

    @screenshot_file_id.setter
    def screenshot_file_id(self, v):
        self._d["screenshot_file_id"] = v

    @property
    def tx_hash(self):
        return self._d.get("tx_hash")

    @tx_hash.setter
    def tx_hash(self, v):
        self._d["tx_hash"] = v

    @property
    def requested_at(self):
        return self._d.get("requested_at")

    @property
    def verified_at(self):
        return self._d.get("verified_at")

    @property
    def admin_notes(self):
        return self._d.get("admin_notes")

    def to_dict(self):
        return self._d

    @staticmethod
    async def create(user_id, plan, amount, coin, address, username=None):
        from models import get_db
        db = get_db()
        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "username": username,
            "plan": plan,
            "amount": amount,
            "coin": coin,
            "address": address,
            "screenshot_file_id": None,
            "tx_hash": None,
            "status": "pending",
            "verified_by": None,
            "verified_at": None,
            "rejection_reason": None,
            "admin_notes": None,
            "requested_at": now,
        }
        result = await db[PaymentRequest.collection_name].insert_one(doc)
        doc["_id"] = result.inserted_id
        return PaymentRequest(doc)

    async def save(self):
        from models import get_db
        db = get_db()
        await db[PaymentRequest.collection_name].update_one(
            {"_id": self.id}, {"$set": self._d}
        )

    @staticmethod
    async def find_pending():
        from models import get_db
        db = get_db()
        cursor = db[PaymentRequest.collection_name].find(
            {"status": {"$in": ["pending", "screenshot_received", "manual_check"]}}
        ).sort("requested_at", -1)
        docs = await cursor.to_list(length=None)
        return [PaymentRequest(d) for d in docs]

    @staticmethod
    async def find_by_id(pid):
        from models import get_db
        db = get_db()
        doc = await db[PaymentRequest.collection_name].find_one({"_id": pid})
        return PaymentRequest(doc) if doc else None


async def grant_plan(user_id, plan_name):
    """Upgrade a user to a paid plan (payment confirmed).

    Shared by the admin Verify button and automatic on-chain verification.
    Returns (user, plan_dict) or (None, None) if the user doesn't exist.
    """
    from models.user import User
    from config.constants import UPGRADE_PLANS

    user = await User.find_one(user_id)
    if not user:
        return None, None
    plan = UPGRADE_PLANS.get(plan_name, {})
    user.plan = plan_name
    user.plan_multiplier = plan.get("multiplier", 1)
    user.base_hash_rate = plan.get("hash_rate", 1)
    user.calculate_effective_hash_rate()
    user.pending_payment_id = None
    await user.save()
    return user, plan
