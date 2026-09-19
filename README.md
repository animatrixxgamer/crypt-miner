# CryptoMinerPro — Telegram Mining Bot

A feature-rich Telegram mining bot with a full Telegram-based admin panel. Built with Python, python-telegram-bot, and MongoDB.

## Features

### User Features
- ⛏️ Real-time mining with hash rate display
- 💰 Multiple crypto support (USDT default, BTC, ETH, LTC, SOL)
- 📦 5 upgrade plans (Free → VIP)
- 👥 Referral system with tiered rewards
- 🎁 Daily bonus with streak system
- 🚀 Hash rate boosts (2x, 5x)
- 🏆 20+ achievements
- 🏊 Mining pools (Public, VIP)
- 💸 Withdrawal with auto-proof forwarding
- 📰 Live crypto news feed
- 📊 Detailed statistics & leaderboard

### Admin Panel (Telegram-only)
- 👥 User management (view, edit balance/hash/plan, ban/unban, reset)
- 💸 Withdrawal management (approve/reject with auto TX hash)
- 💳 Payment verification
- 📢 Broadcast messages to all users
- 🪂 Airdrop coins to users
- ⚙️ Edit ALL settings (mining, rewards, boosts, referrals, etc.)
- 🪙 Edit coin prices
- 📢 Force-join channel configuration
- 📨 Withdrawal proof group configuration
- 📈 Mining configuration (interval, rewards, bonuses)
- 🔒 Maintenance mode toggle
- 🏊 Pool management

## Setup

1. Clone and install:
```bash
pip install -r requirements.txt
```

2. Configure `.env` (copy from `.env.example`):
```bash
cp .env.example .env
# Edit .env with your values
```

3. Run:
```bash
python main.py
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token |
| `BOT_NAME` | Bot display name |
| `BOT_USERNAME` | Bot username |
| `MONGODB_URI` | MongoDB connection string |
| `ADMIN_IDS` | Comma-separated Telegram user IDs |
| `WALLET_USDT_TRC20` | Your USDT TRC20 wallet |
| `WALLET_USDT_ERC20` | Your USDT ERC20 wallet |

## Admin Commands

Use `/admin` in the bot to access the admin panel. Only configured admin IDs can use it.

## Deploying to a Server

See `DEPLOYMENT.md` for full step-by-step instructions (VPS, Railway, Render). In short:

```bash
pip install -r requirements.txt
python main.py
```

Your config lives in `.env` (bot token, MongoDB URI, admin IDs, wallets). The bot reads `BOT_TOKEN` from `.env` — if you regenerate the token in BotFather, update `.env` **and restart the bot**, or every button will stop working.

## Project Structure

```
├── main.py              # Entry point
├── bot.py               # User-facing bot commands & mining engine
├── admin.py             # Telegram admin panel
├── config/
│   └── constants.py     # All settings, plans, coin info, achievements
├── models/
│   ├── __init__.py      # MongoDB connection
│   ├── user.py          # User model
│   ├── withdrawal.py    # Withdrawal model
│   ├── payment_request.py # Payment model
│   ├── mining_session.py  # Mining session model
│   ├── referral.py      # Referral model
│   └── pool.py          # Pool model
├── requirements.txt
├── .env.example
└── .env
```
