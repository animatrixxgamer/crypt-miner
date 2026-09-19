"""
CryptoMinerPro — Entry Point
Connects to MongoDB and launches the Telegram bot.

Hardened for running as a background child process (e.g. inside a
Telegram hosting bot): retries MongoDB + polling, clears leftover
webhooks, and reports startup status directly to the configured
admin(s) via Telegram so it never fails silently.
"""
import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv
# override=True: the .env file must ALWAYS win over inherited env vars
# (a hosting server may export its own BOT_TOKEN / MONGODB_URI, which would
# otherwise silently replace our config and break the bot).
load_dotenv(override=True)

from models import connect_db, close_db, ensure_db_schema


async def notify_admins(app, text):
    """Send a status message to every configured admin via Telegram."""
    from config.constants import all_staff_ids
    for admin_id in all_staff_ids():
        try:
            await app.bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception:
            pass


async def main():
    logger = logging.getLogger(__name__)
    logger.info("Starting CryptoMinerPro...")

    from bot import build_bot
    app = build_bot()

    # ── Connect to Telegram first (retry with backoff) ──
    for attempt in range(5):
        try:
            await app.initialize()
            break
        except Exception as e:
            wait = (attempt + 1) * 10
            logger.warning(f"Init failed (attempt {attempt+1}/5): {e}. Retrying in {wait}s...")
            await asyncio.sleep(wait)
    else:
        logger.error("❌ Could not connect to Telegram after 5 attempts. Check internet/token.")
        sys.exit(1)

    # ── Connect to MongoDB (retry — a slow first-time Atlas connection must not kill the bot) ──
    connected = False
    for attempt in range(5):
        try:
            await connect_db()
            await ensure_db_schema()
            connected = True
            logger.info("✅ Connected to MongoDB")
            break
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed (attempt {attempt+1}/5): {e}")
            if attempt < 4:
                await asyncio.sleep(10)
    if not connected:
        logger.error("❌ MongoDB unreachable after 5 attempts.")
        await notify_admins(
            app,
            "❌ *CryptoMinerPro failed to start:* MongoDB is unreachable.\n"
            "Check `MONGODB_URI` in `.env` and allow your server's IP in MongoDB Network Access.",
        )
        sys.exit(1)

    await app.start()

    # ── Clear any leftover webhook — polling silently fails with "Conflict" otherwise ──
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook cleared (if any) — polling mode active")
    except Exception as e:
        logger.warning(f"⚠️ Could not clear webhook: {e}")

    # ── Start polling with retry ──
    # Handles "Conflict: terminated by other getUpdates request" that happens when
    # another instance (or the hosting bot's 5s pre-check) briefly held the connection.
    started = False
    for attempt in range(5):
        try:
            await app.updater.start_polling(drop_pending_updates=True)
            started = True
            break
        except Exception as e:
            logger.warning(f"⚠️ Polling start failed (attempt {attempt+1}/5): {e}. Retrying in 10s...")
            try:
                await app.updater.stop()
            except Exception:
                pass
            await asyncio.sleep(10)
    if not started:
        logger.error("❌ Could not start polling after 5 attempts.")
        await notify_admins(
            app,
            "❌ *CryptoMinerPro failed to start:* Telegram polling conflict.\n"
            "Another instance of this bot is running with the same token — stop it first.",
        )
        sys.exit(1)

    # ── Report live status to console AND to admins ──
    try:
        me = await app.bot.get_me()
        logger.info(f"✅ BOT IS LIVE as @{me.username} (id {me.id}) — message it now")
        await notify_admins(
            app,
            f"✅ *CryptoMinerPro is LIVE* as @{me.username}\n\n"
            "• MongoDB: connected\n• Polling: active\n\n"
            "Send /start to the bot to test.",
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not fetch bot info: {e}")

    # ── Start auto-proof generator with RANDOM timing ──
    if app.job_queue is not None:
        from admin import schedule_next_proof, proof_watchdog
        app.job_queue.run_once(schedule_next_proof, when=60)  # first proof after 1 min
        # Hourly watchdog: if the self-rescheduling chain ever dies (an
        # exception, a dropped job, a host hiccup), re-arm it so proofs
        # don't silently stop days later.
        app.job_queue.run_repeating(proof_watchdog, interval=3600, first=3600, name="auto_proof_watchdog")
        logger.info("✅ Auto-proof job started (random intervals) + hourly watchdog")
    else:
        logger.warning("⚠️ job-queue extra missing — auto-proof disabled. Install python-telegram-bot[job-queue].")

    # ── Keep running ──
    stop_event = asyncio.Event()

    def signal_handler():
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()

    # Graceful shutdown
    logger.info("Shutting down...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    await close_db()
    logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())