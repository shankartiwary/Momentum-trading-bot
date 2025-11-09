import streamlit as st
from angelone import AngelBroker
from survivor import SurvivorStrategy
import logging
import time
import yaml
import os
import threading
import pandas as pd
from queue import Queue, Empty

# --- Initial Setup ---
st.set_page_config(layout="wide")
st.title("Survivor Trading Bot")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- State Management & Helper Classes ---
class AppState:
    @staticmethod
    def initialize():
        if 'initialized' not in st.session_state:
            st.session_state.initialized = True
            st.session_state.logged_in = False
            st.session_state.bot_running = False
            st.session_state.broker = None
            st.session_state.trading_thread = None
            st.session_state.log_queue = Queue()
            st.session_state.ltp_queue = Queue()
            st.session_state.orders = []
            st.session_state.dry_run = True

class QueueLogHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue
    def emit(self, record):
        self.queue.put(self.format(record))

class OrderManager:
    def add_order(self, order):
        st.session_state.orders.append(order)

# --- Initialize Session State ---
AppState.initialize()

# --- Trading Logic Thread ---
def trading_loop(broker, config, log_queue, ltp_queue):
    thread_logger = logging.getLogger(__name__ + "_thread")
    handler = QueueLogHandler(log_queue)
    thread_logger.addHandler(handler)
    thread_logger.setLevel(logging.INFO)

    order_manager = OrderManager()
    strategy = SurvivorStrategy(broker, config, order_manager)

    thread_logger.info("Trading loop started.")

    while st.session_state.get('bot_running', False):
        try:
            ltp = broker.fut_ltp()
            if ltp:
                ltp_queue.put(ltp)
                strategy.on_ticks_update({'last_price': ltp})
            else:
                thread_logger.warning("Could not fetch LTP.")
            time.sleep(10)
        except Exception as e:
            thread_logger.error(f"Error in trading loop: {e}", exc_info=True)
            time.sleep(10)

    thread_logger.info("Trading loop stopped.")

# --- Streamlit UI Components ---
def show_login_page():
    st.header("Login to Angel One")
    with st.form("login_form"):
        api_key = st.text_input("API Key", type="password")
        client_code = st.text_input("Client Code")
        password = st.text_input("Password", type="password")
        totp_secret = st.text_input("TOTP Secret", type="password")
        dry_run = st.checkbox("Dry Run (Simulation Mode)", value=st.session_state.get('dry_run', True))

        if st.form_submit_button("Login"):
            if not all([api_key, client_code, password, totp_secret]) and not dry_run:
                st.error("Please fill in all fields for live trading.")
            else:
                with st.spinner("Logging in..."):
                    try:
                        st.session_state.dry_run = dry_run
                        broker = AngelBroker(
                            api_key=api_key, client_code=client_code,
                            password=password, totp_secret=totp_secret,
                            dry_run=dry_run, logger=logger
                        )
                        broker.login()
                        if broker.is_connected() or dry_run:
                            st.session_state.broker = broker
                            st.session_state.logged_in = True
                            st.success("Login successful!")
                            st.experimental_rerun()
                        else:
                            st.error("Live login failed. Check credentials.")
                    except Exception as e:
                        st.error(f"An error occurred: {e}")

def show_dashboard():
    st.header("Dashboard")
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Controls")
        if st.button("Start Bot", disabled=st.session_state.bot_running):
            config_file = "survivor.yml"
            if not os.path.exists(config_file):
                st.error("survivor.yml not found!")
            else:
                with open(config_file, 'r') as f: config = yaml.safe_load(f).get('default', {})
                st.session_state.bot_running = True
                thread = threading.Thread(
                    target=trading_loop,
                    args=(st.session_state.broker, config, st.session_state.log_queue, st.session_state.ltp_queue),
                    daemon=True
                )
                st.session_state.trading_thread = thread
                thread.start()
                st.experimental_rerun()

        if st.button("Stop Bot", disabled=not st.session_state.bot_running):
            st.session_state.bot_running = False
            if st.session_state.trading_thread: st.session_state.trading_thread.join(timeout=5)
            st.experimental_rerun()

        if st.button("Logout"):
            st.session_state.clear()
            st.experimental_rerun()

        st.subheader("Status")
        status_color = "green" if st.session_state.bot_running else "red"
        st.markdown(f"**Bot Status:** <span style='color:{status_color};'>{'Running' if st.session_state.bot_running else 'Stopped'}</span>", unsafe_allow_html=True)
        mode = "Dry Run" if st.session_state.dry_run else "Live Trading"
        st.markdown(f"**Mode:** {mode}")
        st.subheader("NIFTY Future LTP")
        ltp_placeholder = st.empty()

    with col2:
        st.subheader("Logs")
        log_placeholder = st.empty()
        st.subheader("Trade Book")
        orders_placeholder = st.empty()

    try:
        latest_ltp = st.session_state.ltp_queue.get_nowait()
        st.session_state.last_ltp = latest_ltp
    except Empty:
        pass # Keep last known LTP if queue is empty

    if 'last_ltp' in st.session_state:
        ltp_placeholder.metric("NIFTY Future", f"{st.session_state.last_ltp:.2f}")

    log_messages = []
    while not st.session_state.log_queue.empty():
        log_messages.append(st.session_state.log_queue.get_nowait())
    if 'log_history' not in st.session_state: st.session_state.log_history = []
    st.session_state.log_history.extend(log_messages)
    log_display = "\\n".join(st.session_state.log_history[-20:])
    log_placeholder.text_area("Live Logs", log_display, height=300)

    if st.session_state.orders:
        orders_placeholder.dataframe(pd.DataFrame(st.session_state.orders))
    else:
        orders_placeholder.info("No trades executed yet.")

# --- Main App Logic ---
if st.session_state.logged_in:
    show_dashboard()
    time.sleep(2)
    try:
        st.experimental_rerun()
    except st.errors.RerunException:
        pass
else:
    show_login_page()
