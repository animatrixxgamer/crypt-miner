"""
Withdrawal model — MongoDB via Motor async.
"""
from datetime import datetime, timezone


class Withdrawal:
    collection_name = "withdrawals"

    def __init__(self, data: dict):
        self._d = data

    @property
    def id(self):
        return self._d.get("_id")

    @property
    def user_id(self):
        return self._d.get("user_id")

    @property
    def username(self):
        return self._d.get("username")

    @property
    def amount(self):
        return self._d.get("amount", 0)

    @property
    def address(self):
        return self._d.get("address", "")

    @property
    def coin(self):
        return self._d.get("coin", "USDT")

    @property
    def tx_hash(self):
        return self._d.get("tx_hash")

    @tx_hash.setter
    def tx_hash(self, v):
        self._d["tx_hash"] = v

    @property
    def status(self):
        return self._d.get("status", "pending")

    @status.setter
    def status(self, v):
        self._d["status"] = v

    @property
    def processing_steps(self):
        return self._d.get("processing_steps", [])

    @property
    def requested_at(self):
        return self._d.get("requested_at")

    @property
    def approved_at(self):
        return self._d.get("approved_at")

    @property
    def completed_at(self):
        return self._d.get("completed_at")

    @property
    def rejected_reason(self):
        return self._d.get("rejected_reason")

    @property
    def approved_by(self):
        return self._d.get("approved_by")

    @property
    def plan(self):
        return self._d.get("plan", "Free")

    def add_processing_step(self, step, details=None):
        steps = self._d.get("processing_steps", [])
        steps.append({"step": step, "timestamp": datetime.now(timezone.utc), "details": details})
        self._d["processing_steps"] = steps

    def to_dict(self):
        return self._d

    @staticmethod
    async def create(user_id, username, amount, address, coin, plan="Free"):
        from models import get_db
        db = get_db()
        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "username": username,
            "amount": amount,
            "address": address,
            "coin": coin,
            "plan": plan,
            "tx_hash": None,
            "status": "pending",
            "processing_steps": [],
            "requested_at": now,
            "approved_at": None,
            "completed_at": None,
            "rejected_reason": None,
            "approved_by": None,
        }
        result = await db[Withdrawal.collection_name].insert_one(doc)
        doc["_id"] = result.inserted_id
        return Withdrawal(doc)

    async def save(self):
        from models import get_db
        db = get_db()
        await db[Withdrawal.collection_name].update_one(
            {"_id": self.id}, {"$set": self._d}
        )

    @staticmethod
    async def find_pending():
        from models import get_db
        db = get_db()
        cursor = db[Withdrawal.collection_name].find(
            {"status": {"$in": ["pending", "processing"]}}
        ).sort("requested_at", -1)
        docs = await cursor.to_list(length=None)
        return [Withdrawal(d) for d in docs]

    @staticmethod
    async def find_by_id(wid):
        from models import get_db
        db = get_db()
        doc = await db[Withdrawal.collection_name].find_one({"_id": wid})
        return Withdrawal(doc) if doc else None

    @staticmethod
    async def find_by_user(user_id):
        from models import get_db
        db = get_db()
        cursor = db[Withdrawal.collection_name].find(
            {"user_id": user_id}
        ).sort("requested_at", -1)
        docs = await cursor.to_list(length=None)
        return [Withdrawal(d) for d in docs]

    @staticmethod
    async def count_documents(query):
        from models import get_db
        db = get_db()
        return await db[Withdrawal.collection_name].count_documents(query)

    @staticmethod
    async def find_all(query, skip=0, limit=20):
        from models import get_db
        db = get_db()
        cursor = db[Withdrawal.collection_name].find(query).sort("requested_at", -1).skip(skip).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [Withdrawal(d) for d in docs]
