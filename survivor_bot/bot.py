import threading
import time
from .angelone import AngelBroker
from .survivor import SurvivorStrategy

class OrderManager:
    """A simple order manager to track trades."""
    def __init__(self):
        self.orders = []

    def add_order(self, order):
        self.orders.append(order)

class TradingBot(threading.Thread):
    def __init__(self, config, logger, status_queue):
        super().__init__()
        self.daemon = True
        self.config = config
        self.logger = logger
        self.status_queue = status_queue

        self._is_running = False
        self.broker = None
        self.strategy = None
        self.trade_history = []

    def run(self):
        """The main entry point for the trading bot thread."""
        self._is_running = True
        self.logger.info("Trading bot thread started.")

        try:
            self.broker = AngelBroker(
                api_key=self.config['API_KEY'],
                client_code=self.config['CLIENT_CODE'],
                password=self.config['PASSWORD'],
                totp_secret=self.config['TOTP_SECRET'],
                logger=self.logger
            )
            self.broker.login()

            if self.broker.is_connected():
                self.logger.info("Broker connection successful.")
                self.status_queue.put("CONNECTED")

                order_manager = OrderManager()
                self.strategy = SurvivorStrategy(self.broker, self.config['SURVIVOR_CFG'], order_manager)
                self.trade_history = order_manager.orders

                while self._is_running:
                    ltp = self.broker.fut_ltp()
                    if ltp:
                        self.strategy.on_ticks_update({'last_price': ltp})
                    else:
                        self.logger.warning("Could not fetch LTP.")
                    time.sleep(self.config.get('LTP_POLL_SEC', 10))
            else:
                self.logger.error("Broker login failed.")
                self.status_queue.put("DISCONNECTED")

        except Exception as e:
            self.logger.error(f"An error occurred in the trading bot: {e}", exc_info=True)
            self.status_queue.put("DISCONNECTED")

        self.logger.info("Trading bot thread finished.")

    def stop(self):
        """Signals the bot to stop running."""
        self._is_running = False
        self.logger.info("Stop signal received.")

    def get_funds(self):
        """Fetches funds from the broker if connected."""
        if self.broker and self.broker.is_connected():
            return self.broker.get_funds()
        return None

    def fire_test_order(self):
        """Places a test order to verify connectivity."""
        if not self.broker:
            self.logger.warning("Broker not initialized. Cannot fire test order.")
            return

        try:
            self.logger.info("Firing a test order.")
            symbol = f"{self.config['UNDERLYING']}{self.config['EXPIRY']}25000CE"
            instrument = self.broker.get_instrument_details(symbol)
            if not instrument:
                self.logger.error(f"Test order failed: Could not find instrument {symbol}")
                return

            token = instrument['token']
            order_id, msg = self.broker._place_order(symbol, token, "BUY", 1)

            if order_id:
                self.logger.info(f"Test order placed successfully: {order_id}")
                self.trade_history.append({'timestamp': time.time(), 'symbol': symbol, 'status': 'SUCCESS', 'order_id': order_id})
            else:
                self.logger.error(f"Test order failed: {msg}")
                self.trade_history.append({'timestamp': time.time(), 'symbol': symbol, 'status': f'FAILED: {msg}'})
        except Exception as e:
            self.logger.error(f"Exception during test order: {e}", exc_info=True)
