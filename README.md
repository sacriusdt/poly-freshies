# Poly Freshies

Detect large trades from fresh wallets on Polymarket and flag possible insiders. It polls the public API every 10 seconds, filters trades, and posts alerts to the CLI or a Telegram channel.

## What it checks

1. Only trades above the minimum size (default: 2000 USDC).
2. Price must be 0.50 or below.
3. Title must not contain blacklisted keywords (default: bitcoin, solana, ethereum, xrp).
4. Market tags must not include `sports`.
5. Wallet must have a low number of predictions (default: 10 or less).

## Output format

```
🟢 🔥 YES [Some Market Title](https://polymarket.com/event/some-event) [3]
23.50% | `4200` USDC | 4th predictions by [user](https://polymarket.com/@user) [2]
```

- Green is BUY, red is SELL.
- Rank emoji is based on size:
  - 2,000 to 5,000 USDC: 🦈
  - 5,000 to 10,000 USDC: 🐬
  - above 10,000 USDC: 🐳

## Install

```
pip install -r requirements.txt
```

## Run (CLI only)

```
python poly_freshies.py
```

While it runs, you can type commands directly in the same terminal:

- `/help` or `/` to list commands
- `/size <number>`
- `/predictions <number>`
- `/blkey <keywords>`
- `/bluser <wallet_or_name>`

Common options:

```
python poly_freshies.py --min-size 3000 --max-predictions 8
```

You can also use environment variables:

- `POLY_MIN_SIZE`
- `POLY_MAX_PREDICTIONS`

## Run with Telegram

Set these env vars:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID` (channel id, usually starts with `-100`)
- `TELEGRAM_ADMIN_ID` (your user id, used to restrict commands)

Then run:

```
python poly_freshies.py --telegram
```

The bot can send messages to a channel where it is admin. Commands are only accepted from `TELEGRAM_ADMIN_ID`.

### Admin commands

- `/start` → `🟢 Poly Freshies started.`
- `/size <number>` → update minimum size
- `/predictions <number>` → update max predictions allowed
- `/blkey <keywords>` → add keywords to the title blacklist
- `/bluser <wallet_or_name>` → add wallets or names to the user blacklist

## Notes

- Market and user data are cached in memory to avoid repeated calls.
- Seen trades are stored in `.poly_freshies_state.json` to avoid duplicate alerts after restart.
