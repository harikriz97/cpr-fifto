"""
v13 CPR Strategy — Angel One Smart API Data Fetcher
=====================================================
Fetches historical OHLC and real-time market data via Angel One Smart API.
"""

import pyotp
import time
import logging
from datetime import datetime, timedelta
from SmartApi import SmartConnect   # pip install smartapi-python

import config

log = logging.getLogger(__name__)

# Angel One symbol token for NIFTY spot
NIFTY_TOKEN = "99926000"
NSE_EXCHANGE = "NSE"
NFO_EXCHANGE = "NFO"


class AngelOneClient:
    def __init__(self):
        self.api = SmartConnect(api_key=config.ANGELONE_API_KEY)
        self.session = None
        self.connected = False

    def login(self):
        """Login using TOTP authentication."""
        totp = pyotp.TOTP(config.ANGELONE_TOTP_KEY).now()
        data = self.api.generateSession(
            config.ANGELONE_CLIENT_ID,
            config.ANGELONE_PASSWORD,
            totp
        )
        if data['status']:
            self.session = data['data']
            self.connected = True
            log.info(f"Angel One login successful: {config.ANGELONE_CLIENT_ID}")
        else:
            raise ConnectionError(f"Angel One login failed: {data['message']}")
        return self.session

    def get_nifty_ohlc_history(self, days=30):
        """
        Fetch NIFTY daily OHLC for last N days.
        Returns list of dicts: [{'date', 'open', 'high', 'low', 'close'}, ...]
        """
        to_date   = datetime.now()
        from_date = to_date - timedelta(days=days + 15)  # extra buffer for EMA
        params = {
            "exchange":    NSE_EXCHANGE,
            "symboltoken": NIFTY_TOKEN,
            "interval":    "ONE_DAY",
            "fromdate":    from_date.strftime("%Y-%m-%d %H:%M"),
            "todate":      to_date.strftime("%Y-%m-%d %H:%M"),
        }
        resp = self.api.getCandleData(params)
        if not resp['status']:
            raise RuntimeError(f"getCandleData failed: {resp['message']}")

        rows = []
        for bar in resp['data']:
            # Angel One format: [timestamp, open, high, low, close, volume]
            rows.append({
                'date':  bar[0][:10],
                'open':  float(bar[1]),
                'high':  float(bar[2]),
                'low':   float(bar[3]),
                'close': float(bar[4]),
            })
        return sorted(rows, key=lambda x: x['date'])

    def get_nifty_ltp(self):
        """Get current NIFTY spot LTP."""
        resp = self.api.ltpData(NSE_EXCHANGE, "Nifty 50", NIFTY_TOKEN)
        if not resp['status']:
            raise RuntimeError(f"LTP fetch failed: {resp['message']}")
        return float(resp['data']['ltp'])

    def get_option_ltp(self, symbol_token, exchange=NFO_EXCHANGE):
        """Get LTP for an option using its symbol token."""
        resp = self.api.ltpData(exchange, "", symbol_token)
        if not resp['status']:
            raise RuntimeError(f"Option LTP failed: {resp['message']}")
        return float(resp['data']['ltp'])

    def search_option_token(self, symbol_name):
        """
        Search for option symbol token.
        symbol_name: e.g. 'NIFTY24JAN2124400CE'
        Returns token string.
        """
        resp = self.api.searchScrip(NFO_EXCHANGE, symbol_name)
        if not resp['status'] or not resp['data']:
            raise RuntimeError(f"Symbol not found: {symbol_name}")
        return resp['data'][0]['symboltoken']

    def get_option_chain_ltp(self, expiry_date, atm_strike, opt_type, strike_type,
                              strike_interval=50):
        """
        Get LTP for a specific option.
        expiry_date: 'DDMMMYYYY' e.g. '23JAN2025' (Angel One format)
        strike_type: 'ATM', 'OTM1', 'ITM1'
        opt_type: 'CE' or 'PE'
        Returns (symbol_name, token, ltp)
        """
        offset = {'ATM': 0, 'OTM1': strike_interval, 'ITM1': -strike_interval}
        if opt_type == 'CE':
            strike = atm_strike + offset[strike_type]
        else:
            strike = atm_strike - offset[strike_type]

        symbol = f"NIFTY{expiry_date}{strike}{opt_type}"
        token  = self.search_option_token(symbol)
        ltp    = self.get_option_ltp(token)
        return symbol, token, ltp

    def get_nifty_1min_ohlc(self, from_dt, to_dt):
        """Fetch NIFTY 1-minute OHLC for intraday use."""
        params = {
            "exchange":    NSE_EXCHANGE,
            "symboltoken": NIFTY_TOKEN,
            "interval":    "ONE_MINUTE",
            "fromdate":    from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate":      to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        resp = self.api.getCandleData(params)
        if not resp['status']:
            raise RuntimeError(f"1-min OHLC failed: {resp['message']}")
        return resp['data']

    @staticmethod
    def expiry_to_angelone_format(expiry_yymmdd):
        """
        Convert expiry YYMMDD (e.g. '260421') to Angel One format ('21APR2026').
        """
        from datetime import datetime
        dt = datetime.strptime('20' + expiry_yymmdd, '%Y%m%d')
        return dt.strftime('%d%b%Y').upper()
