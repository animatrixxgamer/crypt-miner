"""
MongoDB connection manager using Motor (async driver).
"""
import os
import logging
import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv(override=True)  # .env always wins over inherited env vars

logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/mining_bot")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "mining_bot")

_client = None
_db = None


async def connect_db():
    global _client, _db
    _client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    _db = _client[MONGODB_DB_NAME]
    await _db.command("ping")
    return _db


# ── Schema migration ────────────────────────────────────────────────
# Older bot versions stored ids as camelCase (userId, chatId…) and left a
# UNIQUE index like `userId_1` behind. New documents write snake_case
# (user_id), so the missing camelCase field defaults to null and the
# second insert crashes with:
#   DuplicateKeyError: E11000 ... index: userId_1 dup key: { userId: null }
# Fix: drop every leftover index that contains a legacy camelCase field
# (harmless — nothing writes those fields anymore), then make sure the
# current unique index on `user_id` exists.
_LEGACY_INDEX_KEYS = {"userId", "userName", "username", "chatId", "withdrawalId",
                      "requestId", "paymentId", "sessionId", "poolId",
                      "referralId", "refereeId", "referrerId",
                      "referralCode", "referral_code"}
_OWNED_COLLECTIONS = ["users", "withdrawals", "payment_requests",
                      "mining_sessions", "pools", "referrals"]


async def ensure_db_schema():
    """Drop stale camelCase/legacy unique indexes and ensure the snake_case
    one. Safe to run on every boot — index ops are idempotent."""
    db = get_db()
    if db is None:
        return
    for coll_name in _OWNED_COLLECTIONS:
        coll = db[coll_name]
        try:
            indexes = await coll.index_information()
        except Exception as e:
            logger.warning(f"schema: could not list indexes on {coll_name}: {e}")
            continue
        for name, spec in indexes.items():
            if name == "_id_":
                continue
            keys = [k for k, _ in (spec.get("key") or [])]
            # Drop any index that still references a legacy camelCase field.
            if any(k in _LEGACY_INDEX_KEYS for k in keys):
                try:
                    await coll.drop_index(name)
                    logger.info(f"schema: dropped stale index {coll_name}.{name} ({keys})")
                except Exception as e:
                    logger.warning(f"schema: could not drop {coll_name}.{name}: {e}")
            # On the users collection the ONLY unique index this bot needs is
            # `user_id`. Any other unique index (userId, referralCode, …) is a
            # leftover from an older schema and will DuplicateKeyError the
            # moment a second user registers (null dup key). Drop them all.
            elif coll_name == "users" and spec.get("unique") and "user_id" not in keys:
                try:
                    await coll.drop_index(name)
                    logger.info(f"schema: dropped stale unique index users.{name} ({keys})")
                except Exception as e:
                    logger.warning(f"schema: could not drop users.{name}: {e}")
        # Ensure the current unique key exists (best-effort: a pre-existing
        # duplicate would make Mongo refuse, which we log and continue).
        if coll_name == "users":
            try:
                await coll.create_index([("user_id", 1)], unique=True)
            except Exception as e:
                logger.warning(f"schema: could not ensure unique user_id index: {e}")


def get_db():
    return _db


async def close_db():
    global _client
    if _client:
        _client.close()
