import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import requests

TRADES_URL = "https://data-api.polymarket.com/trades"
TRADED_URL = "https://data-api.polymarket.com/traded"
MARKET_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
STATE_FILE = ".poly_freshies_state.json"

EVENT_LINK = "https://polymarket.com/event/{slug}"
USER_LINK = "https://polymarket.com/@{name}"

DEFAULT_KEYWORDS = {"bitcoin", "solana", "ethereum", "xrp"}


@dataclass
class Settings:
    min_size: float = 2000.0
    max_price: float = 0.50
    max_predictions: int = 10
    poll_seconds: int = 10
    blacklist_keywords: Set[str] = field(default_factory=lambda: set(DEFAULT_KEYWORDS))
    blacklist_users: Set[str] = field(default_factory=set)
    telegram_enabled: bool = False
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_admin_id: Optional[int] = None


class TelegramClient:
    def __init__(self, token: str, chat_id: str, admin_id: Optional[int]) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.admin_id = admin_id
        self.last_update_id: Optional[int] = None
        self.session = requests.Session()

    def send(self, text: str) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            self.session.post(f"{self.base_url}/sendMessage", json=payload, timeout=10)
        except requests.RequestException as exc:
            log(f"Telegram send failed: {exc}")

    def get_updates(self) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"timeout": 0}
        if self.last_update_id is not None:
            params["offset"] = self.last_update_id
        try:
            resp = self.session.get(f"{self.base_url}/getUpdates", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
        except requests.RequestException as exc:
            log(f"Telegram getUpdates failed: {exc}")
            return []

    def handle_updates(self, settings: Settings) -> None:
        updates = self.get_updates()
        for update in updates:
            update_id = update.get("update_id")
            if update_id is not None:
                self.last_update_id = update_id + 1
            message = update.get("message") or update.get("channel_post")
            if not message:
                continue
            text = (message.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            from_user = message.get("from") or {}
            from_id = from_user.get("id")
            if settings.telegram_admin_id is not None and from_id != settings.telegram_admin_id:
                continue
            response = handle_command(text, settings)
            if response:
                self.send(response)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        log(f"Failed to read .env: {exc}")


def parse_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def add_keywords(target: Set[str], raw: str) -> List[str]:
    items: List[str] = []
    for part in raw.replace(",", " ").split():
        clean = part.strip().lower()
        if clean:
            target.add(clean)
            items.append(clean)
    return items


def add_users(target: Set[str], raw: str) -> List[str]:
    items: List[str] = []
    for part in raw.replace(",", " ").split():
        clean = part.strip().lower()
        if clean:
            target.add(clean)
            items.append(clean)
    return items


def command_help_text() -> str:
    return (
        "Commands:\n"
        "/start\n"
        "/size <number>\n"
        "/predictions <number>\n"
        "/blkey <keywords>\n"
        "/bluser <wallet_or_name>\n"
        "/help"
    )


def handle_command(text: str, settings: Settings) -> Optional[str]:
    cleaned = text.strip()
    if not cleaned:
        return None
    if cleaned == "/" or cleaned.lower() == "/help":
        return command_help_text()
    if not cleaned.startswith("/"):
        return None

    parts = cleaned.split()
    command = parts[0].lower()
    args = parts[1:]

    if command == "/start":
        return "🟢 Poly Freshies started."

    if command == "/size":
        if not args:
            return "Missing size value."
        value = parse_float(args[0])
        if value is None:
            return "Invalid size value."
        settings.min_size = value
        return f"Minimum size set to {int(settings.min_size)} USDC."

    if command == "/predictions":
        if not args:
            return "Missing predictions value."
        value = parse_int(args[0])
        if value is None:
            return "Invalid predictions value."
        settings.max_predictions = value
        return f"Max predictions set to {settings.max_predictions}."

    if command == "/blkey":
        if not args:
            return "Missing keywords value."
        added = add_keywords(settings.blacklist_keywords, " ".join(args))
        return f"Keywords added: {', '.join(added)}"

    if command == "/bluser":
        if not args:
            return "Missing user value."
        added = add_users(settings.blacklist_users, " ".join(args))
        return f"Users added: {', '.join(added)}"

    return "Unknown command or missing value."


def start_cli_listener(settings: Settings) -> None:
    def loop() -> None:
        print("CLI commands enabled. Type /help for the list.")
        while True:
            try:
                line = sys.stdin.readline()
            except Exception as exc:
                log(f"CLI input failed: {exc}")
                break
            if not line:
                time.sleep(0.2)
                continue
            response = handle_command(line, settings)
            if response:
                print(response)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def load_seen_state(max_seen: int) -> deque:
    seen_queue: deque[str] = deque()
    if not os.path.exists(STATE_FILE):
        return seen_queue
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        items = data.get("seen") if isinstance(data, dict) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    seen_queue.append(item)
        while len(seen_queue) > max_seen:
            seen_queue.popleft()
    except (OSError, json.JSONDecodeError) as exc:
        log(f"State load failed: {exc}")
    return seen_queue


def save_seen_state(seen_queue: deque) -> None:
    try:
        payload = {"seen": list(seen_queue)}
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError as exc:
        log(f"State save failed: {exc}")


def escape_md(text: str) -> str:
    return text.replace("[", "(").replace("]", ")")


def rank_emoji(size: float) -> str:
    if size > 10000:
        return "🐳"
    if size >= 5000:
        return "🐬"
    return "🦈"


def is_crypto_address(value: str) -> bool:
    if not value.startswith("0x"):
        return False
    hex_part = value[2:]
    if len(hex_part) < 8:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in hex_part)


def format_display_name(name: str) -> str:
    if is_crypto_address(name):
        short = f"0x{name[2:6]}"
        return f"@{short}"
    return f"@{name}"


def format_notification(trade: Dict[str, Any], event_count: int, user_count: int, traded: int) -> str:
    side = (trade.get("side") or "").upper()
    side_emoji = "🟢" if side == "BUY" else "🔴"
    size = float(trade.get("size") or 0)
    price = float(trade.get("price") or 0)
    price_pct = f"{price * 100:.2f}%"
    size_int = int(round(size))
    size_fmt = f"{size_int:,}"

    outcome = escape_md(trade.get("outcome") or "")
    title = escape_md(trade.get("title") or "")
    event_slug = trade.get("eventSlug") or ""
    event_link = EVENT_LINK.format(slug=event_slug)
    title_text = f"{outcome} | {title}" if title else outcome

    name = trade.get("name") or trade.get("pseudonym") or trade.get("proxyWallet") or "unknown"
    name_safe = escape_md(name)
    if name_safe != "unknown":
        name_link = USER_LINK.format(name=name_safe)
        display = format_display_name(name_safe)
        name_text = f"[{display}]({name_link})"
    else:
        name_text = "@unknown"

    rank = rank_emoji(size)

    line1 = f"{side_emoji}{rank} {title_text}"
    line2 = f"[event]({event_link})"
    line3 = f"`{price_pct}` | `{size_fmt}` USDC | {traded}th predictions by {name_text}"
    return f"{line1}\n{line2}\n\n{line3}"


def fetch_trades(session: requests.Session, settings: Settings) -> List[Dict[str, Any]]:
    params = {
        "limit": 50,
        "takerOnly": "true",
        "filterType": "CASH",
        "filterAmount": int(settings.min_size),
    }
    try:
        resp = session.get(TRADES_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except requests.RequestException as exc:
        log(f"Trades fetch failed: {exc}")
        return []


def fetch_market_tags(session: requests.Session, slug: str) -> Optional[Set[str]]:
    if not slug:
        return None
    try:
        resp = session.get(MARKET_URL.format(slug=slug), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tags = data.get("tags") or []
        result: Set[str] = set()
        for tag in tags:
            label = (tag.get("label") or "").lower()
            slug_tag = (tag.get("slug") or "").lower()
            if label:
                result.add(label)
            if slug_tag:
                result.add(slug_tag)
        return result
    except requests.RequestException as exc:
        log(f"Market fetch failed for {slug}: {exc}")
        return None


def fetch_user_traded(session: requests.Session, wallet: str) -> Optional[int]:
    if not wallet:
        return None
    try:
        resp = session.get(TRADED_URL, params={"user": wallet}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        traded = data.get("traded")
        if traded is None:
            return None
        return int(traded)
    except requests.RequestException as exc:
        log(f"User traded fetch failed for {wallet}: {exc}")
        return None


def should_skip_by_title(title: str, blacklist: Set[str]) -> bool:
    title_lower = title.lower()
    return any(word in title_lower for word in blacklist)


def user_in_blacklist(trade: Dict[str, Any], blacklist: Set[str]) -> bool:
    wallet = (trade.get("proxyWallet") or "").lower()
    name = (trade.get("name") or "").lower()
    return wallet in blacklist or name in blacklist


def build_settings(args: argparse.Namespace) -> Settings:
    settings = Settings()

    env_size = os.getenv("POLY_MIN_SIZE")
    env_pred = os.getenv("POLY_MAX_PREDICTIONS")

    if env_size:
        value = parse_float(env_size)
        if value is not None:
            settings.min_size = value
    if env_pred:
        value = parse_int(env_pred)
        if value is not None:
            settings.max_predictions = value

    if args.min_size is not None:
        settings.min_size = args.min_size
    if args.max_predictions is not None:
        settings.max_predictions = args.max_predictions
    if args.poll_seconds is not None:
        settings.poll_seconds = args.poll_seconds

    if args.blacklist:
        add_keywords(settings.blacklist_keywords, args.blacklist)
    if args.blacklist_users:
        add_users(settings.blacklist_users, args.blacklist_users)

    telegram_token = args.telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = args.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")
    telegram_admin_id = args.telegram_admin_id or os.getenv("TELEGRAM_ADMIN_ID")

    if telegram_admin_id is not None:
        try:
            telegram_admin_id = int(telegram_admin_id)
        except ValueError:
            telegram_admin_id = None

    if args.telegram or (telegram_token and telegram_chat_id):
        settings.telegram_enabled = True
        settings.telegram_token = telegram_token
        settings.telegram_chat_id = telegram_chat_id
        settings.telegram_admin_id = telegram_admin_id

    return settings


def run() -> None:
    parser = argparse.ArgumentParser(description="Detect large fresh wallet bets on Polymarket.")
    parser.add_argument("--min-size", type=float, help="Minimum trade size in USDC.")
    parser.add_argument("--max-predictions", type=int, help="Maximum predictions allowed for a wallet.")
    parser.add_argument("--poll-seconds", type=int, help="Polling interval in seconds.")
    parser.add_argument("--blacklist", type=str, help="Comma or space separated keywords.")
    parser.add_argument("--blacklist-users", type=str, help="Comma or space separated users or wallets.")
    parser.add_argument("--telegram", action="store_true", help="Enable Telegram bot.")
    parser.add_argument("--telegram-token", type=str, help="Telegram bot token.")
    parser.add_argument("--telegram-chat-id", type=str, help="Telegram chat id.")
    parser.add_argument("--telegram-admin-id", type=str, help="Telegram admin user id.")

    args = parser.parse_args()
    load_dotenv()
    settings = build_settings(args)

    if settings.telegram_enabled and (not settings.telegram_token or not settings.telegram_chat_id):
        log("Telegram enabled but token or chat id missing.")
        settings.telegram_enabled = False

    session = requests.Session()
    telegram_client: Optional[TelegramClient] = None
    if settings.telegram_enabled:
        telegram_client = TelegramClient(settings.telegram_token, settings.telegram_chat_id, settings.telegram_admin_id)

    market_cache: Dict[str, Set[str]] = {}
    user_cache: Dict[str, int] = {}
    event_counts: Dict[str, int] = defaultdict(int)
    user_counts: Dict[str, int] = defaultdict(int)

    max_seen = 5000
    seen_queue = load_seen_state(max_seen)
    seen_set: Set[str] = set(seen_queue)
    pending_state_save = False

    last_poll = 0.0
    log("Poly Freshies is running.")
    start_cli_listener(settings)

    while True:
        now = time.time()
        if now - last_poll >= settings.poll_seconds:
            last_poll = now
            trades = fetch_trades(session, settings)
            for trade in trades:
                tx = trade.get("transactionHash") or f"{trade.get('proxyWallet')}-{trade.get('timestamp')}-{trade.get('size')}"
                if tx in seen_set:
                    continue
                seen_set.add(tx)
                seen_queue.append(tx)
                if len(seen_queue) > max_seen:
                    old = seen_queue.popleft()
                    seen_set.discard(old)
                pending_state_save = True

                price = float(trade.get("price") or 0)
                if price > settings.max_price:
                    continue

                title = trade.get("title") or ""
                if should_skip_by_title(title, settings.blacklist_keywords):
                    continue

                if user_in_blacklist(trade, settings.blacklist_users):
                    continue

                slug = trade.get("eventSlug") or ""
                if slug in market_cache:
                    tags = market_cache[slug]
                else:
                    tags = fetch_market_tags(session, slug)
                    if tags is None:
                        continue
                    market_cache[slug] = tags

                if "sports" in tags:
                    continue

                wallet = trade.get("proxyWallet") or ""
                if wallet in user_cache:
                    traded = user_cache[wallet]
                else:
                    traded = fetch_user_traded(session, wallet)
                    if traded is None:
                        continue
                    user_cache[wallet] = traded

                if traded > settings.max_predictions:
                    continue

                event_counts[slug] += 1
                user_counts[wallet] += 1
                message = format_notification(trade, event_counts[slug], user_counts[wallet], traded)
                print(message)
                if telegram_client:
                    telegram_client.send(message)

            if pending_state_save:
                save_seen_state(seen_queue)
                pending_state_save = False

        if telegram_client:
            telegram_client.handle_updates(settings)

        time.sleep(1)


if __name__ == "__main__":
    run()
