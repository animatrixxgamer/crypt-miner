"""
MiningSession model — MongoDB via Motor async.
"""
from datetime import datetime, timezone


class MiningSession:
    collection_name = "mining_sessions"

    def __init__(self, data: dict):
        self._d = data

    @property
    def id(self):
        return self._d.get("_id")

    @property
    def user_id(self):
        return self._d.get("user_id")

    @property
    def session_id(self):
        return self._d.get("_id")

    def to_dict(self):
        return self._d

    @staticmethod
    async def create(user_id, username, hash_rate):
        from models import get_db
        db = get_db()
        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "username": username,
            "start_time": now,
            "end_time": None,
            "duration": None,
            "hash_rate": hash_rate,
            "coins_mined": 0.0,
            "blocks_found": 0,
            "created_at": now,
        }
        result = await db[MiningSession.collection_name].insert_one(doc)
        doc["_id"] = result.inserted_id
        return MiningSession(doc)

    async def end_session(self, total_mined, blocks_found, duration):
        from models import get_db
        db = get_db()
        now = datetime.now(timezone.utc)
        await db[MiningSession.collection_name].update_one(
            {"_id": self.id},
            {"$set": {
                "end_time": now,
                "duration": duration,
                "coins_mined": round(total_mined, 8),
                "blocks_found": blocks_found,
            }},
        )

    @staticmethod
    async def get_user_sessions(user_id, limit=10):
        from models import get_db
        db = get_db()
        cursor = db[MiningSession.collection_name].find(
            {"user_id": user_id}
        ).sort("start_time", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [MiningSession(d) for d in docs]

    @staticmethod
    async def get_recent_all(limit=10):
        from models import get_db
        db = get_db()
        cursor = db[MiningSession.collection_name].find(
            {}, {"_id": 0, "user_id": 1, "username": 1, "coins_mined": 1, "start_time": 1, "hash_rate": 1}
        ).sort("start_time", -1).limit(limit)
        return await cursor.to_list(length=limit)
