"""
live/telegram_alert.py — Telegram trade alerts for FIFTO
=========================================================
Sends real-time notifications to your Telegram:
  - Trade entry (agent, option, strike, lots, entry price, target, SL)
  - Trade exit (reason, P&L)
  - P&L update every 15 minutes during active trade
  - S4 watching / Contra watching notifications
  - EOD summary

Setup:
  1. Create a bot: message @BotFather → /newbot → get token
  2. Send /start to your new bot
  3. Run: python live/telegram_alert.py --setup  (prints your chat_id)
  4. Add token + chat_id to live/config.py
"""
from __future__ import annotations
import os
import sys
import logging
import threading
import time
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

try:
    from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_TOKEN   = ""
    TELEGRAM_CHAT_ID = ""


# ── Core send ──────────────────────────────────────────────────────────────────
def send(text: str, parse_mode: str = "HTML") -> bool:
    """Send message to Telegram. Returns True on success."""
    token   = TELEGRAM_TOKEN   or os.getenv("TELEGRAM_TOKEN", "")
    chat_id = TELEGRAM_CHAT_ID or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=5,
        )
        if not r.json().get("ok"):
            logger.warning("Telegram send failed: %s", r.json().get("description"))
            return False
        return True
    except Exception as e:
        logger.warning("Telegram error: %s", e)
        return False


def get_chat_id() -> str | None:
    """Fetch chat_id from most recent message to the bot."""
    token = TELEGRAM_TOKEN or os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=5
        )
        updates = r.json().get("result", [])
        if updates:
            msg  = updates[-1].get("message", updates[-1].get("channel_post", {}))
            chat = msg.get("chat", {})
            return str(chat.get("id", ""))
        return None
    except Exception as e:
        logger.warning("get_chat_id failed: %s", e)
        return None


# ── Formatted alerts ───────────────────────────────────────────────────────────
AGENT_EMOJI = {
    "THOR":      "⚡",
    "HULK":      "💚",
    "IRON MAN":  "🟡",
    "CAPTAIN":   "🟣",
    "CRT":       "🟠",
    "MRC":       "🩷",
    "S4_2nd":    "🔁",
    "contra_sl": "↩️",
}

def alert_entry(signal: str, strategy: str, opt: str, strike: int,
                expiry: str, lots: int, ep: float, tgt: float, hsl: float,
                score: int, zone: str, bias: str, dte: int):
    """Trade entry alert."""
    emoji = AGENT_EMOJI.get(signal, "📊")
    tgt_pct = round((ep - tgt) / ep * 100, 1) if ep > 0 else 0
    sl_pct  = round((hsl - ep) / ep * 100, 1) if ep > 0 else 0
    text = (
        f"{emoji} <b>ENTRY — {signal}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎯 Option   : <b>NIFTY {expiry} {strike} {opt}</b>\n"
        f"📍 Zone     : {zone}  |  {bias.upper()}\n"
        f"⏱ DTE      : {dte} days\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 Entry    : <b>Rs.{ep:.2f}</b>\n"
        f"✅ Target   : Rs.{tgt:.2f}  ({tgt_pct}% decay)\n"
        f"🛑 Hard SL  : Rs.{hsl:.2f}  (+{sl_pct}%)\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📦 Lots     : {lots}x  (score {score}/7)\n"
        f"🕐 Time     : {datetime.now().strftime('%H:%M:%S')}"
    )
    send(text)


def alert_exit(signal: str, opt: str, strike: int, ep: float,
               xp: float, reason: str, pnl: float, lots: int):
    """Trade exit alert."""
    is_win = pnl > 0
    emoji  = "✅" if is_win else "❌"
    reason_map = {
        "target":    "🎯 Target Hit",
        "lockin_sl": "🔒 Lock-in SL",
        "hard_sl":   "🛑 Hard SL",
        "eod":       "🕐 EOD Exit",
        "trail_sl":  "📉 Trail SL",
    }
    reason_str = reason_map.get(reason, reason)
    pnl_sign   = "+" if pnl >= 0 else ""
    text = (
        f"{emoji} <b>EXIT — {signal}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📌 {reason_str}\n"
        f"🎯 Option   : {strike} {opt}\n"
        f"💰 Entry    : Rs.{ep:.2f}\n"
        f"💸 Exit     : Rs.{xp:.2f}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<b>P&L : {pnl_sign}Rs.{pnl:,.0f}</b>  ({lots} lots)\n"
        f"🕐 Time     : {datetime.now().strftime('%H:%M:%S')}"
    )
    send(text)


def alert_pnl_update(signal: str, opt: str, strike: int,
                     ep: float, current: float, upnl: float,
                     decay_pct: float, trail_label: str, lots: int):
    """15-minute P&L update during active trade."""
    is_profit = upnl >= 0
    arrow     = "📈" if is_profit else "📉"
    sign      = "+" if is_profit else ""
    text = (
        f"{arrow} <b>P&L UPDATE — {signal}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎯 {strike} {opt}  |  {lots} lots\n"
        f"💰 Entry : Rs.{ep:.2f}\n"
        f"📊 Now   : Rs.{current:.2f}  ({decay_pct:+.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<b>uP&L : {sign}Rs.{upnl:,.0f}</b>\n"
        f"🔒 Trail : {trail_label}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    send(text)


def alert_s4_watching(opt: str, strike: int, ep: float,
                      lo: float, hi: float):
    """S4 re-entry watch started."""
    send(
        f"🔁 <b>S4 WATCHING</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Same option: {strike} {opt}\n"
        f"Entry was  : Rs.{ep:.2f}\n"
        f"Watch band : Rs.{lo:.0f} – Rs.{hi:.0f}\n"
        f"Expires    : 14:00:00"
    )


def alert_contra_watching(opt: str, spot_at_exit: float):
    """Contra trade watch started after hard SL."""
    send(
        f"↩️ <b>CONTRA WATCHING</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Sell {opt} on spot pullback\n"
        f"Spot at SL : {spot_at_exit:.2f}\n"
        f"Tolerance  : ±30 pts\n"
        f"Expires    : 14:00:00"
    )


def alert_eod_summary(trades: list[dict]):
    """EOD summary of all trades."""
    if not trades:
        send("🌙 <b>EOD — No trades today</b>")
        return
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    n_win     = sum(1 for t in trades if t.get("pnl", 0) > 0)
    n_trades  = len(trades)
    emoji     = "✅" if total_pnl >= 0 else "❌"
    sign      = "+" if total_pnl >= 0 else ""
    lines     = [f"{emoji} <b>EOD SUMMARY</b>"]
    lines.append("━━━━━━━━━━━━━━━━━")
    for t in trades:
        p   = t.get("pnl", 0)
        sig = t.get("signal", t.get("strategy", "?"))
        ps  = "+" if p >= 0 else ""
        lines.append(
            f"{'✅' if p>=0 else '❌'} {sig}  {t.get('opt','')} {t.get('strike','')}  "
            f"{ps}Rs.{p:,.0f}"
        )
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append(f"<b>Total : {sign}Rs.{total_pnl:,.0f}</b>  |  {n_win}/{n_trades} wins")
    send("\n".join(lines))


# ── 15-min P&L update thread ─────────────────────────────────────────────────
_pnl_thread: threading.Thread | None = None
_pnl_stop   = threading.Event()

def start_pnl_updates(get_state_fn, interval_secs: int = 900):
    """
    Start background thread that sends P&L update every 15 min.
    get_state_fn() must return dict with keys:
      signal, opt, strike, ep, current, upnl, decay_pct, trail_label, lots
    or None when no active trade.
    """
    global _pnl_thread, _pnl_stop
    _pnl_stop.clear()

    def _run():
        while not _pnl_stop.wait(interval_secs):
            try:
                s = get_state_fn()
                if s and s.get("status") == "open":
                    alert_pnl_update(
                        signal      = s.get("signal", ""),
                        opt         = s.get("opt", ""),
                        strike      = s.get("strike", 0),
                        ep          = float(s.get("entry", 0)),
                        current     = float(s.get("current", 0)),
                        upnl        = float(s.get("upnl", 0)),
                        decay_pct   = float(s.get("decay_pct", 0)),
                        trail_label = s.get("trail_label", "None"),
                        lots        = int(s.get("lots", 1)),
                    )
            except Exception as e:
                logger.debug("P&L update thread error: %s", e)

    _pnl_thread = threading.Thread(target=_run, daemon=True)
    _pnl_thread.start()
    logger.info("Telegram P&L update thread started (every %ds)", interval_secs)

def stop_pnl_updates():
    _pnl_stop.set()


# ── Setup helper ──────────────────────────────────────────────────────────────
def setup():
    """Print setup instructions and auto-detect chat_id."""
    print("\nTelegram Bot Setup")
    print("==================")
    token = TELEGRAM_TOKEN or input("Enter bot token: ").strip()
    print(f"\nUsing token: {token[:20]}...")
    print("\n1. Open Telegram")
    print("2. Search for your bot")
    print("3. Send /start to it")
    input("Press Enter after sending /start...")

    global TELEGRAM_TOKEN
    TELEGRAM_TOKEN = token
    chat_id = get_chat_id()
    if chat_id:
        print(f"\nchat_id = {chat_id}")
        print(f"\nAdd to live/config.py:")
        print(f'TELEGRAM_TOKEN   = "{token}"')
        print(f'TELEGRAM_CHAT_ID = "{chat_id}"')
        # Test message
        from importlib import import_module
        import config
        config.TELEGRAM_TOKEN   = token
        config.TELEGRAM_CHAT_ID = chat_id
        send("FIFTO bot connected! Trade alerts active.")
        print("\nTest message sent!")
    else:
        print("\nNo chat_id found. Make sure you sent /start to the bot.")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    else:
        print("Usage: python live/telegram_alert.py --setup")
