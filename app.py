import streamlit as st
import pandas as pd
import time
import queue
import logging
import sys
from survivor_bot.bot import TradingBot

# --- Helper for logging ---
class QueueLogHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        self.queue.put(self.format(record))

class StreamlitLogger:
    def __init__(self, queue):
        self.log_queue = queue
        self.logger = logging.getLogger('TradingBotLogger')
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            queue_handler = QueueLogHandler(self.log_queue)
            queue_handler.setFormatter(formatter)
            self.logger.addHandler(queue_handler)
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

    def info(self, msg, *args, **kwargs): self.logger.info(msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs): self.logger.warning(msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs): self.logger.error(msg, *args, **kwargs)

# --- Streamlit App Layout ---
st.set_page_config(layout="wide")
st.title("Trading Bot Controller")

# --- Session State Initialization ---
if 'bot' not in st.session_state: st.session_state.bot = None
if 'log_queue' not in st.session_state: st.session_state.log_queue = queue.Queue()
if 'logger' not in st.session_state: st.session_state.logger = StreamlitLogger(st.session_state.log_queue)
if 'status_queue' not in st.session_state: st.session_state.status_queue = queue.Queue()
if 'is_connected' not in st.session_state: st.session_state.is_connected = False

# --- Sidebar for Configuration ---
with st.sidebar:
    st.header("Configuration")
    with st.expander("API Credentials", expanded=True):
        api_key = st.text_input("API Key", "YOUR_API_KEY")
        client_code = st.text_input("Client Code", "YOUR_CLIENT_CODE")
        password = st.text_input("Password", "YOUR_PASSWORD", type="password")
        totp_secret = st.text_input("TOTP Secret", "YOUR_TOTP_SECRET")
    with st.expander("Trading Parameters", expanded=True):
        underlying = st.text_input("Underlying", "NIFTY")
        expiry = st.text_input("Expiry (e.g., 26DEC24)", "26DEC24")
    with st.expander("Survivor Config"):
        survivor_cfg_params = {
            'pe_gap': st.number_input("Survivor: PE Gap", value=100),
            'ce_gap': st.number_input("Survivor: CE Gap", value=100),
            'pe_symbol_gap': st.number_input("Survivor: PE Symbol Gap", value=200),
            'ce_symbol_gap': st.number_input("Survivor: CE Symbol Gap", value=200),
            'pe_quantity': st.number_input("Survivor: PE Lots", value=1),
            'ce_quantity': st.number_input("Survivor: CE Lots", value=1),
        }

# --- Main App Area ---
col1, col2, col3 = st.columns(3)
if col1.button("Start Bot"):
    if st.session_state.bot is None:
        bot_config = {
            'API_KEY': api_key, 'CLIENT_CODE': client_code, 'PASSWORD': password, 'TOTP_SECRET': totp_secret,
            'UNDERLYING': underlying, 'EXPIRY': expiry, 'SURVIVOR_CFG': survivor_cfg_params
        }
        st.session_state.is_connected = False
        st.session_state.bot = TradingBot(bot_config, st.session_state.logger, st.session_state.status_queue)
        st.session_state.bot.start()
        st.success("Bot started successfully!")
    else:
        st.warning("Bot is already running.")

if col2.button("Stop Bot"):
    if st.session_state.bot:
        st.session_state.bot.stop()
        st.session_state.bot = None
        st.info("Bot stopped.")
    else:
        st.warning("Bot is not running.")

if col3.button("Fire Test Order"):
    if st.session_state.bot and st.session_state.is_connected:
        st.session_state.bot.fire_test_order()
    else:
        st.warning("Bot must be running and connected.")

# --- Display Status, Logs, and Trades ---
status_indicator = st.empty()
margin_col1, margin_col2 = st.columns(2)
available_margin_ph = margin_col1.empty()
used_margin_ph = margin_col2.empty()
st.subheader("Trade History")
trade_history_placeholder = st.empty()
log_area = st.empty()

# --- Update Loop ---
if not st.session_state.status_queue.empty():
    if st.session_state.status_queue.get() == "CONNECTED":
        st.session_state.is_connected = True

if st.session_state.bot and st.session_state.bot.is_alive():
    if st.session_state.is_connected:
        status_indicator.markdown('<span style="color:green">●</span> Connected to Broker', unsafe_allow_html=True)
        funds = st.session_state.bot.get_funds()
        if funds:
            available_margin_ph.metric("Available Margin", f"₹ {funds['available']:,.2f}")
            used_margin_ph.metric("Used Margin", f"₹ {funds['used']:,.2f}")
    else:
        status_indicator.markdown('<span style="color:red">●</span> Disconnected', unsafe_allow_html=True)
else:
    status_indicator.markdown('<span style="color:red">●</span> Disconnected', unsafe_allow_html=True)

if st.session_state.bot:
    if st.session_state.bot.trade_history:
        trade_history_placeholder.dataframe(pd.DataFrame(st.session_state.bot.trade_history))
    else:
        trade_history_placeholder.info("No trades yet.")

log_messages = []
while not st.session_state.log_queue.empty():
    log_messages.insert(0, st.session_state.log_queue.get())
log_area.text_area("Live Logs", "\\n".join(log_messages), height=400)

time.sleep(2)
st.rerun()
