"""
live/orders.py — OpenAlgo order management + 3-tier trailing SL
Handles: place, modify, cancel orders + SL tracking per tick.
"""
from __future__ import annotations
import logging
import requests
from datetime import datetime
from config import OPENALGO_HOST, OPENALGO_API_KEY, INDICES

log = logging.getLogger("orders")


# ── OpenAlgo REST helpers ─────────────────────────────────────────────────────
def _post(endpoint: str, payload: dict) -> dict:
    """POST to OpenAlgo API. Returns response JSON. Raises on HTTP error."""
    url = f"{OPENALGO_HOST}/api/v1/{endpoint}"
    headers = {"Content-Type": "application/json"}
    payload.setdefault("apikey", OPENALGO_API_KEY)   # body-based auth (OpenAlgo standard)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            log.error("OpenAlgo error [%s]: %s", endpoint, data.get("message"))
        return data
    except requests.exceptions.Timeout:
        log.error("OpenAlgo timeout on %s", endpoint)
        raise
    except requests.exceptions.ConnectionError:
        log.error("OpenAlgo unreachable — is the server running at %s?", OPENALGO_HOST)
        raise
    except requests.exceptions.HTTPError as e:
        log.error("OpenAlgo HTTP %s on %s: %s", resp.status_code, endpoint, resp.text)
        raise


def _get(endpoint: str, params: dict = None) -> dict:
    """GET from OpenAlgo API."""
    url = f"{OPENALGO_HOST}/api/v1/{endpoint}"
    headers = {"x-api-key": OPENALGO_API_KEY}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        log.error("OpenAlgo GET timeout on %s", endpoint)
        raise
    except requests.exceptions.ConnectionError:
        log.error("OpenAlgo unreachable on GET %s", endpoint)
        raise


# ── Symbol helpers ────────────────────────────────────────────────────────────
def build_option_symbol(index: str, expiry_date: str, strike: int, opt: str) -> str:
    """
    Build option symbol for OpenAlgo.
    index:       'NIFTY' or 'SENSEX'
    expiry_date: 'YYYYMMDD'
    strike:      integer strike price
    opt:         'CE' or 'PE'
    Returns e.g. 'NIFTY20261002550CE'
    """
    exp = expiry_date[2:]   # YYMMDD
    return f"{index}{exp}{strike}{opt}"


def get_atm_strike(spot: float, index: str) -> int:
    """Round spot to nearest strike interval for the index."""
    si = INDICES[index]["strike_int"]
    return int(round(spot / si) * si)


def get_otm_strike(spot: float, index: str, opt: str, steps: int = 1) -> int:
    """OTM1/ITM1 strike. For PE sell OTM = lower strike, for CE sell OTM = higher."""
    si  = INDICES[index]["strike_int"]
    atm = get_atm_strike(spot, index)
    if opt == "PE":
        return atm - si * steps   # OTM for PE sell
    else:
        return atm + si * steps   # OTM for CE sell


def get_strike(spot: float, index: str, opt: str, stype: str) -> int:
    """Return strike for ATM / OTM1 / ITM1."""
    if stype == "ATM":
        return get_atm_strike(spot, index)
    if stype == "OTM1":
        return get_otm_strike(spot, index, opt, steps=1)
    if stype == "ITM1":
        # ITM = opposite direction of OTM
        si  = INDICES[index]["strike_int"]
        atm = get_atm_strike(spot, index)
        return atm + si if opt == "PE" else atm - si
    raise ValueError(f"Unknown strike type: {stype}")


# ── Order placement ───────────────────────────────────────────────────────────
def place_sell_order(index: str, expiry: str, strike: int, opt: str,
                     lots: int, strategy_tag: str = "") -> dict:
    """
    Place SELL order for option via OpenAlgo.
    Returns response dict with order_id.
    """
    cfg    = INDICES[index]
    symbol = build_option_symbol(index, expiry, strike, opt)
    qty    = lots * cfg["lot_size"]

    payload = {
        "apikey":     OPENALGO_API_KEY,
        "strategy":   strategy_tag or "hari-cpr",
        "symbol":     symbol,
        "action":     "SELL",
        "exchange":   cfg["exchange"],
        "pricetype":  "MARKET",
        "product":    "MIS",
        "quantity":   str(qty),
    }
    log.info("PLACE SELL %s qty=%d lots=%d", symbol, qty, lots)
    resp = _post("placeorder", payload)
    log.info("Order response: %s", resp)
    return resp


def place_buy_order(index: str, expiry: str, strike: int, opt: str,
                    lots: int, strategy_tag: str = "") -> dict:
    """Place BUY order (for exit / cover)."""
    cfg    = INDICES[index]
    symbol = build_option_symbol(index, expiry, strike, opt)
    qty    = lots * cfg["lot_size"]

    payload = {
        "apikey":     OPENALGO_API_KEY,
        "strategy":   strategy_tag or "hari-cpr",
        "symbol":     symbol,
        "action":     "BUY",
        "exchange":   cfg["exchange"],
        "pricetype":  "MARKET",
        "product":    "MIS",
        "quantity":   str(qty),
    }
    log.info("PLACE BUY (EXIT) %s qty=%d", symbol, qty)
    resp = _post("placeorder", payload)
    log.info("Exit order response: %s", resp)
    return resp


def get_ltp(index: str, expiry: str, strike: int, opt: str) -> float | None:
    """Fetch current LTP for an option via OpenAlgo quotes endpoint."""
    cfg    = INDICES[index]
    symbol = build_option_symbol(index, expiry, strike, opt)
    try:
        # Try POST quotes first (body-auth), fallback to GET
        resp = _post("quotes", {"symbol": symbol, "exchange": cfg["exchange"]})
        ltp  = resp.get("data", {}).get("ltp") or resp.get("ltp", 0)
        return float(ltp) or None
    except Exception:
        try:
            resp = _get("quotes", {"symbol": symbol, "exchange": cfg["exchange"]})
            return float(resp.get("ltp", 0)) or None
        except Exception:
            return None


def get_spot_ltp(index: str) -> float | None:
    """Fetch current NIFTY/SENSEX spot LTP via OpenAlgo."""
    spot_sym = INDICES[index]["spot_sym"]
    exchange = "NSE" if index == "NIFTY" else "BSE"
    try:
        resp = _post("quotes", {"symbol": spot_sym, "exchange": exchange})
        ltp  = resp.get("data", {}).get("ltp") or resp.get("ltp", 0)
        return float(ltp) or None
    except Exception:
        try:
            resp = _get("quotes", {"symbol": spot_sym, "exchange": exchange})
            return float(resp.get("ltp", 0)) or None
        except Exception:
            return None


# ── 3-tier trailing SL manager ────────────────────────────────────────────────
class TrailingSL:
    """
    Manages the 3-tier lock-in trailing SL for a live option sell trade.

    Tiers (based on how much the option price has dropped from entry):
      Tier 1: drop >= 25% → SL = entry price (breakeven)
      Tier 2: drop >= 40% → SL = entry * 0.80
      Tier 3: drop >= 60% → SL trails at entry * (1 - max_drop * 0.95)

    Usage:
        sl = TrailingSL(entry_price=100.0, sl_pct=1.0)
        for tick_price in live_feed:
            result = sl.update(tick_price)
            if result == 'hit':
                exit_trade()
    """
    def __init__(self, entry_price: float, sl_pct: float, target_pct: float):
        self.ep       = entry_price
        self.hard_sl  = round(entry_price * (1 + sl_pct), 2)   # initial SL (above entry for sell)
        self.sl       = self.hard_sl
        self.target   = round(entry_price * (1 - target_pct), 2)
        self.max_drop = 0.0   # max fraction drop seen so far
        self.locked   = False

    def update(self, current_price: float) -> str | None:
        """
        Call on every tick.
        Returns:
          'target'    — target hit
          'hard_sl'   — initial SL hit (no lock-in yet)
          'lockin_sl' — lock-in SL hit
          None        — still open
        """
        cp = current_price

        # Check target
        if cp <= self.target:
            return "target"

        # Update max_drop
        drop = (self.ep - cp) / self.ep
        if drop > self.max_drop:
            self.max_drop = drop

        # Update SL based on tiers
        if self.max_drop >= 0.60:
            new_sl = round(self.ep * (1 - self.max_drop * 0.95), 2)
            if new_sl < self.sl:
                self.sl = new_sl
                self.locked = True
        elif self.max_drop >= 0.40:
            new_sl = round(self.ep * 0.80, 2)
            if new_sl < self.sl:
                self.sl = new_sl
                self.locked = True
        elif self.max_drop >= 0.25:
            new_sl = self.ep   # breakeven
            if new_sl < self.sl:
                self.sl = new_sl
                self.locked = True

        # Check SL
        if cp >= self.sl:
            return "lockin_sl" if self.locked else "hard_sl"

        return None

    def current_sl(self) -> float:
        return self.sl

    def summary(self) -> str:
        return (f"ep={self.ep:.2f} tgt={self.target:.2f} sl={self.sl:.2f} "
                f"max_drop={self.max_drop*100:.1f}% locked={self.locked}")
