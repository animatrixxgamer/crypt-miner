"""
Referral model — MongoDB via Motor async.
"""
from datetime import datetime, timezone


class Referral:
    collection_name = "referrals"

    def __init__(self, data: dict):
        self._d = data

    @property
    def earnings(self):
        return self._d.get("earnings", 0.0)

    @earnings.setter
    def earnings(self, v):
        self._d["earnings"] = v

    def to_dict(self):
        return self._d

    @staticmethod
    async def create(referrer_id, referee_id, referee_username=None):
        from models import get_db
        db = get_db()
        doc = {
            "referrer_id": referrer_id,
            "referee_id": referee_id,
            "referee_username": referee_username,
            "earnings": 0.0,
            "created_at": datetime.now(timezone.utc),
        }
        result = await db[Referral.collection_name].insert_one(doc)
        doc["_id"] = result.inserted_id
        return Referral(doc)

    async def save(self):
        from models import get_db
        db = get_db()
        await db[Referral.collection_name].update_one(
            {"_id": self._d["_id"]}, {"$set": self._d}
        )

    @staticmethod
    async def find_by_referrer(user_id):
        from models import get_db
        db = get_db()
        cursor = db[Referral.collection_name].find({"referrer_id": user_id})
        docs = await cursor.to_list(length=None)
        return [Referral(d) for d in docs]

    @staticmethod
    async def find_one_referee(referee_id):
        from models import get_db
        db = get_db()
        doc = await db[Referral.collection_name].find_one({"referee_id": referee_id})
        return Referral(doc) if doc else None
