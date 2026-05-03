"""
angel_client.py — Angel One SmartAPI wrapper
=============================================
Handles: login, historical OHLC, LTP, option token lookup, WebSocket ticks.

Angel One SmartAPI docs: https://smartapi.angelbroking.com/docs

Data needed each day:
  1. 45 days daily OHLC of NIFTY spot → CPR, EMA, PDH/PDL
  2. NIFTY futures LTP at 09:15 → fut_basis_pts
  3. Real-time NIFTY spot ticks (WebSocket) → IB, 5M/15M candles
  4. Option chain → ATM strike + nearest expiry token
  5. Real-time option LTP (WebSocket) → trade monitoring
"""

import time
import json
import logging
import threading
import requests
import pandas as pd
from datetime import datetime, timedelta

try:
    from SmartApi import SmartConnect
    import pyotp
    SMARTAPI_AVAILABLE = True
except ImportError:
    SMARTAPI_AVAILABLE = False
    SmartConnect = None

logger = logging.getLogger(__name__)

# ── Angel One exchange codes ───────────────────────────────────────────────────
NSE  = "NSE"
NFO  = "NFO"
BSE  = "BSE"

# ── Instrument tokens (NSE segment) ───────────────────────────────────────────
NIFTY_SPOT_TOKEN    = "99926000"  # NIFTY 50 — historical OHLC (getCandleData)
NIFTY_WS_TOKEN      = "26000"     # NIFTY 50 — WebSocket real-time ticks
NIFTY_FUT_SYMBOL    = "NIFTY"   # Nifty near-month futures, NFO

# WebSocket modes
MODE_LTP    = 1   # Last traded price only
MODE_QUOTE  = 2   # LTP + best bid/ask
MODE_SNAP   = 3   # Full snapshot

INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)


class AngelClient:
    """
    Wraps SmartConnect + WebSocket for paper trading needs.
    Call connect() once at startup, then use other methods freely.
    """

    def __init__(self, api_key, client_id, password, totp_secret):
        self.api_key      = api_key
        self.client_id    = client_id
        self.password     = password
        self.totp_secret  = totp_secret
        self.api          = None
        self.auth_token   = None
        self.feed_token   = None
        self._ws          = None
        self._tick_cb     = None
        self._scrip_df    = None   # instrument master

    # ── Authentication ─────────────────────────────────────────────────────────
    def connect(self):
        """Login to Angel One. Must be called before any other method."""
        if not SMARTAPI_AVAILABLE:
            raise ImportError("smartapi-python not installed. Run: pip install smartapi-python pyotp")
        self.api = SmartConnect(api_key=self.api_key)
        totp = pyotp.TOTP(self.totp_secret).now()
        data = self.api.generateSession(self.client_id, self.password, totp)
        if data["status"] is False:
            raise ConnectionError(f"Angel One login failed: {data['message']}")
        self.auth_token = data["data"]["jwtToken"]
        self.feed_token = self.api.getfeedToken()
        logger.info("Angel One login successful")
        return self

    # ── Instrument master ──────────────────────────────────────────────────────
    def _load_scrip_master(self):
        """Download instrument master once and cache in memory."""
        if self._scrip_df is not None:
            return
        logger.info("Downloading instrument master...")
        resp = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
        data = resp.json()
        self._scrip_df = pd.DataFrame(data)
        logger.info(f"  {len(self._scrip_df)} instruments loaded")

    def get_nfo_token(self, symbol: str) -> str:
        """
        Resolve NFO option symbol to Angel One token.
        symbol e.g. 'NIFTY26MAY2026CE23000'

        Angel One symbol format in NFO:
          NIFTY26MAY2026C23000 (C/P not CE/PE in master)
        Returns token string or raises KeyError.
        """
        self._load_scrip_master()
        df = self._scrip_df
        mask = (df["exch_seg"] == "NFO") & (df["symbol"] == symbol)
        row = df[mask]
        if row.empty:
            # Try alternate format
            alt = symbol.replace("CE", "C").replace("PE", "P")
            mask2 = (df["exch_seg"] == "NFO") & (df["symbol"] == alt)
            row = df[mask2]
        if row.empty:
            raise KeyError(f"Token not found for: {symbol}")
        return str(row.iloc[0]["token"])

    def get_nearest_expiry(self, index="NIFTY") -> str:
        """
        Returns nearest NFO weekly/monthly expiry date as YYYYMMDD string.
        Looks at currently available option instruments.
        """
        self._load_scrip_master()
        df = self._scrip_df
        today = datetime.today().strftime("%Y%m%d")
        nfo = df[(df["exch_seg"] == "NFO") & (df["name"] == index) &
                 (df["instrumenttype"].isin(["OPTIDX"]))].copy()
        nfo["expiry_dt"] = pd.to_datetime(nfo["expiry"], format="%d%b%Y",
                                          errors="coerce").dt.strftime("%Y%m%d")
        upcoming = nfo[nfo["expiry_dt"] >= today]["expiry_dt"].dropna().unique()
        if len(upcoming) == 0:
            raise ValueError("No upcoming NIFTY expiries found in scrip master")
        return sorted(upcoming)[0]

    def build_option_symbol(self, expiry_yyyymmdd: str, strike: int,
                            opt_type: str) -> str:
        """
        Build Angel One NFO symbol.
        expiry_yyyymmdd: '20260529'
        strike: 23000
        opt_type: 'CE' or 'PE'
        Returns e.g. 'NIFTY29MAY2026CE23000'
        """
        dt = datetime.strptime(expiry_yyyymmdd, "%Y%m%d")
        expiry_str = dt.strftime("%d%b%Y").upper()   # '29MAY2026'
        return f"NIFTY{expiry_str}{opt_type}{strike}"

    # ── Historical OHLC ────────────────────────────────────────────────────────
    def get_daily_ohlc(self, symbol: str, token: str, exchange: str,
                       n_days: int = 45) -> pd.DataFrame:
        """
        Fetch n_days of daily OHLC.
        Returns DataFrame with columns: date, open, high, low, close
        date is string YYYYMMDD.
        """
        to_dt   = datetime.now()
        from_dt = to_dt - timedelta(days=n_days + 30)   # extra buffer for holidays
        params = {
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    "ONE_DAY",
            "fromdate":    from_dt.strftime("%Y-%m-%d 09:15"),
            "todate":      to_dt.strftime("%Y-%m-%d 15:30"),
        }
        resp = self.api.getCandleData(params)
        if resp.get("status") is False:
            raise RuntimeError(f"getCandleData failed: {resp.get('message')}")
        data = resp.get("data") or []
        if not data:
            raise RuntimeError("getCandleData returned empty data")
        rows = []
        for candle in data:
            # candle = [timestamp_str, open, high, low, close, volume]
            try:
                ts = pd.to_datetime(candle[0]).strftime("%Y%m%d")
                rows.append({"date": ts, "open": float(candle[1]),
                             "high": float(candle[2]), "low": float(candle[3]),
                             "close": float(candle[4])})
            except Exception:
                continue
        if not rows:
            raise RuntimeError("No valid candles parsed from response")
        df = pd.DataFrame(rows).sort_values("date").tail(n_days).reset_index(drop=True)
        return df

    def get_intraday_candles(self, symbol: str, token: str, exchange: str,
                             interval: str = "FIVE_MINUTE",
                             from_time: str = None) -> pd.DataFrame:
        """
        Fetch intraday OHLC candles for today.
        interval: 'ONE_MINUTE', 'FIVE_MINUTE', 'FIFTEEN_MINUTE'
        Returns DataFrame: time (HH:MM:SS), open, high, low, close
        """
        today = datetime.now().strftime("%Y-%m-%d")
        params = {
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    interval,
            "fromdate":    f"{today} 09:15",
            "todate":      f"{today} 15:30",
        }
        resp = self.api.getCandleData(params)
        if resp.get("status") is False:
            return pd.DataFrame()
        rows = []
        for c in resp.get("data", []):
            t = pd.to_datetime(c[0]).strftime("%H:%M:%S")
            rows.append({"time": t, "open": c[1], "high": c[2],
                         "low": c[3], "close": c[4]})
        df = pd.DataFrame(rows)
        if from_time and not df.empty:
            df = df[df["time"] >= from_time]
        return df.reset_index(drop=True)

    # ── LTP ────────────────────────────────────────────────────────────────────
    def get_ltp(self, exchange: str, tradingsymbol: str, token: str) -> float:
        """Get last traded price for a single instrument."""
        resp = self.api.ltpData(exchange, tradingsymbol, token)
        if resp.get("status") is False:
            raise RuntimeError(f"ltpData failed for {tradingsymbol}: {resp.get('message')}")
        return float(resp["data"]["ltp"])

    def get_nifty_spot(self) -> float:
        """Convenience: get NIFTY 50 index LTP."""
        return self.get_ltp(NSE, "Nifty 50", NIFTY_SPOT_TOKEN)

    def find_futures_token(self, index: str = "NIFTY") -> tuple[str, str] | tuple[None, None]:
        """Find nearest expiry futures (token, symbol) from scrip master."""
        try:
            self._load_scrip_master()
            df = self._scrip_df
            mask = (
                (df["exch_seg"].str.upper() == "NFO") &
                (df["name"].str.upper() == index.upper()) &
                (df["instrumenttype"].str.upper().isin(["FUTIDX", "FUTSTK"]))
            )
            fut = df[mask].sort_values("expiry")
            if fut.empty:
                return None, None
            token = str(fut.iloc[0]["token"])
            sym   = str(fut.iloc[0]["symbol"])
            logger.info(f"Futures token found: {sym} = {token}")
            return token, sym
        except Exception as e:
            logger.warning(f"find_futures_token failed: {e}")
            return None, None

    # ── WebSocket / Polling ───────────────────────────────────────────────────
    def start_websocket(self, token_list: list, on_tick_callback,
                        mode: int = MODE_LTP, poll_interval: float = 1.0):
        """
        Stream real-time NIFTY spot ticks.
        Tries SmartWebSocketV2 first; falls back to 1-second REST polling.
        on_tick_callback receives: {"token": str, "last_traded_price": float}
        """
        if not SMARTAPI_AVAILABLE:
            raise ImportError("smartapi-python not installed.")

        # ── Try WebSocket V2 ──────────────────────────────────────────────────
        try:
            from SmartApi.SmartWebSocketV2 import SmartWebSocketV2
            self._ws = SmartWebSocketV2(
                self.auth_token, self.api_key,
                self.client_id, self.feed_token
            )

            def _on_data(wsapp, message):
                try: on_tick_callback(message)
                except Exception as e: logger.error("Tick callback error: %s", e)

            def _on_open(wsapp):
                logger.info("WebSocket V2 connected, subscribing...")
                self._ws.subscribe("nifty_live", mode, token_list)

            self._ws.on_data  = _on_data
            self._ws.on_open  = _on_open
            self._ws.on_error = lambda ws, e: logger.error("WS error: %s", e)
            self._ws.on_close = lambda ws: logger.info("WS closed")

            t = threading.Thread(target=self._ws.connect, daemon=True)
            t.start()
            logger.info("WebSocket V2 started")
            return

        except (ImportError, Exception) as e:
            logger.warning("WebSocket V2 unavailable (%s) — using polling", e)

        # ── Fallback: polling every poll_interval seconds ─────────────────────
        logger.info("Starting REST polling (%.1fs interval)", poll_interval)

        def _poll():
            while True:
                try:
                    spot = self.get_nifty_spot()
                    on_tick_callback({
                        "token": NIFTY_WS_TOKEN,
                        "last_traded_price": spot,   # already in points, not paise
                    })
                except Exception as e:
                    logger.warning("Poll error: %s", e)
                time.sleep(poll_interval)

        t = threading.Thread(target=_poll, daemon=True)
        t.start()
        logger.info("REST polling started")
        logger.info("WebSocket thread started")

    def add_subscription(self, token_list: list, mode: int = MODE_LTP):
        """Subscribe additional tokens after WebSocket is already running."""
        if self._ws:
            self._ws.subscribe("add_sub", mode, token_list)

    def stop_websocket(self):
        if self._ws:
            self._ws.close_connection()
