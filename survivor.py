import os
import sys
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

class SurvivorStrategy:
    """
    Survivor Options Trading Strategy for Angel One (Single-Leg Version).
    """
    
    def __init__(self, broker, config, order_manager):
        for key, value in config.items():
            setattr(self, key, value)

        self.broker = broker
        self.order_manager = order_manager
        
        self.broker.underlying = self.underlying
        self.broker.expiry = self.expiry.upper()

        self._initialize_state()
        
    def _nifty_quote(self):
        return self.broker.fut_ltp()

    def _initialize_state(self):
        self.pe_reset_gap_flag = 0
        self.ce_reset_gap_flag = 0
        
        current_quote = self._nifty_quote()
        if not current_quote:
            logger.error("Could not fetch initial NIFTY quote. Retrying.")
            time.sleep(5)
            current_quote = self._nifty_quote()
            if not current_quote:
                raise ConnectionError("Failed to initialize Nifty quote.")

        self.nifty_pe_last_value = self.pe_start_point or current_quote
        self.nifty_ce_last_value = self.ce_start_point or current_quote
            
        logger.info(f"Init PE Start: {self.nifty_pe_last_value}, Init CE Start: {self.nifty_ce_last_value}")

    def on_ticks_update(self, ticks):
        current_price = ticks['last_price']
        self._handle_pe_trade(current_price)
        self._handle_ce_trade(current_price)
        self._reset_reference_values(current_price)

    def _check_sell_multiplier_breach(self, sell_multiplier):
        if sell_multiplier > self.sell_multiplier_threshold:
            logger.warning(f"Multiplier {sell_multiplier} breached threshold {self.sell_multiplier_threshold}")
            return True
        return False

    def _place_order(self, symbol, quantity, tx_type):
        instrument = self.broker.get_instrument_details(symbol)
        if not instrument:
            logger.error(f"Could not find instrument details for {symbol}")
            return None, "Instrument not found"
        
        token = instrument['token']
        lot_size = int(instrument.get('lotsize', self.nifty_lot_size))
        qty = quantity * lot_size

        return self.broker._place_order(symbol, token, tx_type, qty)

    def _handle_pe_trade(self, current_price):
        if current_price <= self.nifty_pe_last_value:
            return

        price_diff = round(current_price - self.nifty_pe_last_value, 0)
        if price_diff > self.pe_gap:
            sell_multiplier = int(price_diff / self.pe_gap)
            
            if self._check_sell_multiplier_breach(sell_multiplier):
                return

            self.nifty_pe_last_value += self.pe_gap * sell_multiplier
            total_lots = sell_multiplier * self.pe_quantity
            
            strike = self._find_closest_strike(current_price - self.pe_symbol_gap)
            symbol = f"{self.underlying}{self.expiry.upper()}{strike}PE"

            logger.info(f"PE Trade Triggered: Selling {total_lots} lots of {symbol}")

            oid, msg = self._place_order(symbol, total_lots, "SELL")

            if oid:
                self.order_manager.add_order({'id': oid, 'symbol': symbol, 'type': 'SELL_PUT', 'lots': total_lots})
                self.pe_reset_gap_flag = 1
            else:
                logger.error(f"PE order failed: {msg}")

    def _handle_ce_trade(self, current_price):
        if current_price >= self.nifty_ce_last_value:
            return

        price_diff = round(self.nifty_ce_last_value - current_price, 0)
        if price_diff > self.ce_gap:
            sell_multiplier = int(price_diff / self.ce_gap)
            
            if self._check_sell_multiplier_breach(sell_multiplier):
                return

            self.nifty_ce_last_value -= self.ce_gap * sell_multiplier
            total_lots = sell_multiplier * self.ce_quantity
            
            strike = self._find_closest_strike(current_price + self.ce_symbol_gap)
            symbol = f"{self.underlying}{self.expiry.upper()}{strike}CE"
            
            logger.info(f"CE Trade Triggered: Selling {total_lots} lots of {symbol}")

            oid, msg = self._place_order(symbol, total_lots, "SELL")

            if oid:
                self.order_manager.add_order({'id': oid, 'symbol': symbol, 'type': 'SELL_CALL', 'lots': total_lots})
                self.ce_reset_gap_flag = 1
            else:
                logger.error(f"CE order failed: {msg}")

    def _reset_reference_values(self, current_price):
        if self.pe_reset_gap_flag and (self.nifty_pe_last_value - current_price) > self.pe_reset_gap:
            logger.info(f"Resetting PE value from {self.nifty_pe_last_value} to {current_price + self.pe_reset_gap}")
            self.nifty_pe_last_value = current_price + self.pe_reset_gap
            self.pe_reset_gap_flag = 0

        if self.ce_reset_gap_flag and (current_price - self.nifty_ce_last_value) > self.ce_reset_gap:
            logger.info(f"Resetting CE value from {self.nifty_ce_last_value} to {current_price - self.ce_reset_gap}")
            self.nifty_ce_last_value = current_price - self.ce_reset_gap
            self.ce_reset_gap_flag = 0

    def _find_closest_strike(self, target_strike: float) -> int:
        return int(round(target_strike / 50.0)) * 50
