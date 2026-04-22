"""
v13 CPR Strategy — OpenAlgo Paper Trade Client
================================================
Places paper/live orders via OpenAlgo REST API.
OpenAlgo: https://github.com/marketcalls/openalgo
"""

import requests
import logging
import config

log = logging.getLogger(__name__)

class OpenAlgoClient:
    def __init__(self):
        self.host     = config.OPENALGO_HOST
        self.api_key  = config.OPENALGO_API_KEY
        self.strategy = config.OPENALGO_STRATEGY
        self.orders   = {}   # order_id → order details

    def _post(self, endpoint, payload):
        url = f"{self.host}/api/v1/{endpoint}"
        payload['apikey'] = self.api_key
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') != 'success':
            raise RuntimeError(f"OpenAlgo error: {data}")
        return data

    def place_sell_order(self, symbol, quantity, exchange='NFO', product='MIS'):
        """
        Place a SELL (short) market order for options selling.
        symbol: NSE/NFO symbol e.g. 'NIFTY24JAN2524400CE'
        Returns order_id.
        """
        payload = {
            "strategy": self.strategy,
            "symbol":   symbol,
            "action":   "SELL",
            "exchange": exchange,
            "pricetype":"MARKET",
            "product":  product,
            "quantity": str(quantity),
        }
        resp = self._post("placeorder", payload)
        order_id = resp['orderid']
        self.orders[order_id] = {
            'symbol': symbol, 'action': 'SELL', 'qty': quantity, 'status': 'open'
        }
        log.info(f"SELL order placed: {symbol} x{quantity} → order_id={order_id}")
        return order_id

    def place_buy_order(self, symbol, quantity, exchange='NFO', product='MIS'):
        """
        Place a BUY (cover/exit) market order.
        Used to exit a short option position.
        Returns order_id.
        """
        payload = {
            "strategy": self.strategy,
            "symbol":   symbol,
            "action":   "BUY",
            "exchange": exchange,
            "pricetype":"MARKET",
            "product":  product,
            "quantity": str(quantity),
        }
        resp = self._post("placeorder", payload)
        order_id = resp['orderid']
        self.orders[order_id] = {
            'symbol': symbol, 'action': 'BUY', 'qty': quantity, 'status': 'open'
        }
        log.info(f"BUY order placed (exit): {symbol} x{quantity} → order_id={order_id}")
        return order_id

    def get_positions(self):
        """Return current open positions from OpenAlgo."""
        resp = self._post("positions", {})
        return resp.get('data', [])

    def get_order_status(self, order_id):
        """Check status of a specific order."""
        payload = {"orderid": order_id}
        resp = self._post("orderstatus", payload)
        return resp.get('data', {})

    def close_all_positions(self):
        """Emergency: close all open positions."""
        resp = self._post("closeposition", {"strategy": self.strategy})
        log.warning(f"All positions closed: {resp}")
        return resp

    def squareoff(self, symbol, quantity, exchange='NFO', product='MIS'):
        """Alias for place_buy_order — square off a short position."""
        return self.place_buy_order(symbol, quantity, exchange, product)
