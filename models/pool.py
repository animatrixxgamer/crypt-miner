"""
Pool model — MongoDB via Motor async.
"""
from datetime import datetime, timezone


class Pool:
    collection_name = "pools"

    def __init__(self, data: dict):
        self._d = data

    @property
    def id(self):
        return self._d.get("_id")

    @property
    def name(self):
        return self._d.get("name")

    @property
    def pool_type(self):
        return self._d.get("type", "public")

    @property
    def members(self):
        return self._d.get("members", [])

    @property
    def total_hash_rate(self):
        return self._d.get("total_hash_rate", 0.0)

    @total_hash_rate.setter
    def total_hash_rate(self, v):
        self._d["total_hash_rate"] = v

    @property
    def total_mined(self):
        return self._d.get("total_mined", 0.0)

    @total_mined.setter
    def total_mined(self, v):
        self._d["total_mined"] = v

    @property
    def fee(self):
        return self._d.get("fee", 5)

    @fee.setter
    def fee(self, v):
        self._d["fee"] = v

    def to_dict(self):
        return self._d

    async def save(self):
        from models import get_db
        db = get_db()
        await db[Pool.collection_name].update_one(
            {"_id": self.id}, {"$set": self._d}, upsert=True
        )

    @staticmethod
    async def get_public_pool():
        from models import get_db
        db = get_db()
        doc = await db[Pool.collection_name].find_one({"type": "public", "is_active": True})
        if not doc:
            doc = {"name": "Public Mining Pool", "type": "public", "members": [], "total_hash_rate": 0.0, "total_mined": 0.0, "fee": 5, "is_active": True, "created_at": datetime.now(timezone.utc)}
            result = await db[Pool.collection_name].insert_one(doc)
            doc["_id"] = result.inserted_id
        return Pool(doc)

    @staticmethod
    async def get_vip_pool():
        from models import get_db
        db = get_db()
        doc = await db[Pool.collection_name].find_one({"type": "vip", "is_active": True})
        if not doc:
            doc = {"name": "VIP Mining Pool", "type": "vip", "members": [], "total_hash_rate": 0.0, "total_mined": 0.0, "fee": 3, "is_active": True, "created_at": datetime.now(timezone.utc)}
            result = await db[Pool.collection_name].insert_one(doc)
            doc["_id"] = result.inserted_id
        return Pool(doc)

    @staticmethod
    async def get_user_pool(user_id):
        from models import get_db
        db = get_db()
        doc = await db[Pool.collection_name].find_one({"members": user_id, "is_active": True})
        return Pool(doc) if doc else None

    @staticmethod
    async def get_active_pools():
        from models import get_db
        db = get_db()
        cursor = db[Pool.collection_name].find(
            {"is_active": True},
            {"_id": 0, "name": 1, "type": 1, "members": 1, "total_hash_rate": 1, "fee": 1}
        ).sort("total_hash_rate", -1)
        return await cursor.to_list(length=None)
