from datetime import datetime
import math
import time
from typing import Optional, Tuple, Dict
import logging

try:
    from SmartApi import SmartConnect
    import pyotp
except Exception:
    SmartConnect = None
    pyotp = None

class AngelBroker:
    def __init__(self, api_key, client_code, password, totp_secret, logger=None):
        self.api_key = api_key
        self.client_code = client_code
        self.password = password
        self.totp_secret = totp_secret
        self.sc = None
        self.session = {}
        self.underlying = ""
        self.expiry = ""
        self.logger = logger or logging.getLogger(__name__)
        self.instrument_map = {}

    def login(self):
        """
        Logs into the broker.
        """
        if SmartConnect is None:
            self.logger.error("SmartConnect is not installed. Cannot login.")
            return

        self.sc = SmartConnect(api_key=self.api_key)
        try:
            otp = pyotp.TOTP(self.totp_secret).now()
        except Exception:
            raise ValueError("Invalid TOTP Secret. Please provide a valid Base32 key.")

        data = self.sc.generateSession(self.client_code, self.password, otp)
        if "data" not in data or data["data"] is None:
            raise RuntimeError(f"Angel login failed: {data.get('message', 'Unknown error')}")

        self.session = data["data"]
        self.logger.info("[BROKER] Logged in to Angel One.")
        self._fetch_instrument_list()

    def _fetch_instrument_list(self):
        """Downloads the full list of instruments from a static URL and creates a symbol-to-instrument map."""
        try:
            instrument_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            import requests
            response = requests.get(instrument_url)
            if response.status_code == 200:
                instrument_list = response.json()
                for instrument in instrument_list:
                    if 'symbol' in instrument:
                        self.instrument_map[instrument['symbol']] = instrument
                self.logger.info(f"Successfully downloaded and mapped {len(self.instrument_map)} instruments.")
            else:
                self.logger.error(f"Failed to download instrument list. Status code: {response.status_code}")
        except Exception as e:
            self.logger.error(f"Error downloading instrument list: {e}")

    def get_instrument_details(self, symbol: str) -> Optional[dict]:
        return self.instrument_map.get(symbol)

    def get_token(self, symbol: str) -> Optional[str]:
        instrument = self.get_instrument_details(symbol)
        return instrument.get('token') if instrument else None

    def is_connected(self) -> bool:
        return self.session and 'feedToken' in self.session

    def get_funds(self) -> Optional[Dict[str, float]]:
        """Fetches available and used margin from the broker."""
        if not self.is_connected():
            return None

        try:
            rms_data = self.sc.rmsLimit()
            if rms_data and rms_data.get('status') and rms_data.get('data'):
                available_margin = float(rms_data['data'].get('availablecash', 0))
                used_margin = float(rms_data['data'].get('marginused', 0))
                return {'available': available_margin, 'used': used_margin}
            else:
                self.logger.error(f"Failed to fetch RMS data: {rms_data.get('message')}")
                return None
        except Exception as e:
            self.logger.error(f"Exception while fetching funds: {e}")
            return None

    def fut_ltp(self) -> float:
        future_symbol = f"NIFTY{self.expiry}FUT"
        future_token = self.get_token(future_symbol)

        if not future_token:
            self.logger.error(f"Could not find token for future symbol: {future_symbol}")
            return 0.0

        try:
            quote = self.sc.ltpData("NFO", future_symbol, future_token)
            if quote.get('data') and 'ltp' in quote['data']:
                return quote['data']['ltp']
            else:
                self.logger.error(f"Could not fetch LTP for future: {quote}")
                return 0.0
        except Exception as e:
            self.logger.error(f"Exception while fetching future LTP: {e}")
            return 0.0

    def _place_order(self, symbol: str, token: str, tx_type: str, qty: int) -> Tuple[Optional[str], str]:
        """
        Places an order and returns the order ID and a status message.
        """
        try:
            params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": tx_type, "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "CARRYFORWARD", "duration": "DAY", "quantity": str(qty)
            }
            response = self.sc.placeOrder(params)

            if response is None:
                error_message = "API returned no response"
                self.logger.error(f"Failed to place {tx_type} order for {symbol}. Reason: {error_message}")
                return None, error_message

            if response.get('status') and response.get('data', {}).get('orderid'):
                order_id = response['data']['orderid']
                self.logger.info(f"Placed {tx_type} order for {symbol}: {order_id}")
                return order_id, "Success"
            else:
                error_message = response.get('message', 'Unknown error from API')
                self.logger.error(f"Failed to place {tx_type} order for {symbol}. Reason: {error_message}")
                return None, error_message

        except Exception as e:
            error_message = str(e)
            self.logger.error(f"Failed to place {tx_type} order for {symbol}. Exception: {error_message}")
            return None, error_message
