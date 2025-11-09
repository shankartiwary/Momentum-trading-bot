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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

st.set_page_config(layout="wide")

st.title("Survivor Trading Bot")

# --- State Management & Helper Classes ---

class AppState:
    def __init__(self):
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

    @staticmethod
    def get_state():
        return AppState()

class QueueLogHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        self.queue.put(self.format(record))

class OrderManager:
    def __init__(self, app_state):
        self.app_state = app_state

    def add_order(self, order):
        # In a real app, this would be more complex (e.g., tracking status)
        self.app_state.orders.append(order)

# --- Trading Logic Thread ---

def trading_loop(broker, config, log_queue, ltp_queue):
    # Setup logging for this thread
    thread_logger = logging.getLogger(__name__)
    handler = QueueLogHandler(log_queue)
    thread_logger.addHandler(handler)
    thread_logger.setLevel(logging.INFO)

    order_manager = OrderManager(st.session_state)
    strategy = SurvivorStrategy(broker, config, order_manager)

    thread_logger.info("Trading loop started.")

    while st.session_state.get('bot_running', False):
        try:
            ltp = broker.fut_ltp()
            if ltp:
                ltp_queue.put(ltp)
                mock_ticks = {'last_price': ltp}
                strategy.on_ticks_update(mock_ticks)
            else:
                thread_logger.warning("Could not fetch LTP.")
            time.sleep(10) # Poll every 10 seconds
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
        dry_run = st.checkbox("Dry Run (Simulation Mode)", value=st.session_state.dry_run)

        if st.form_submit_button("Login"):
            if not all([api_key, client_code, password, totp_secret]):
                st.error("Please fill in all fields.")
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
                        # For dry run, we can simulate a successful login
                        is_connected = broker.is_connected() or dry_run

                        if is_connected:
                            st.session_state.broker = broker
                            st.session_state.logged_in = True
                            st.success("Login successful!")
                            st.experimental_rerun()
                        else:
                            st.error("Login failed. Check credentials.")
                    except Exception as e:
                        st.error(f"An error occurred: {e}")

def show_dashboard():
    st.header("Dashboard")

    # --- UI Layout ---
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Controls")
        if st.button("Start Bot", disabled=st.session_state.bot_running):
            config_file = "survivor.yml"
            if not os.path.exists(config_file):
                st.error("survivor.yml not found!")
            else:
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f).get('default', {})

                st.session_state.bot_running = True
                thread = threading.Thread(
                    target=trading_loop,
                    args=(st.session_state.broker, config, st.session_state.log_queue, st.session_state.ltp_queue),
                    daemon=True
                )
                st.session_state.trading_thread = thread
                thread.start()
                st.info("Bot starting...")
                st.experimental_rerun()

        if st.button("Stop Bot", disabled=not st.session_state.bot_running):
            st.session_state.bot_running = False
            if st.session_state.trading_thread:
                st.session_state.trading_thread.join(timeout=5)
            st.info("Bot stopping...")
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

    # --- UI Updates (non-blocking) ---

    # Update LTP from queue
    try:
        latest_ltp = st.session_state.ltp_queue.get_nowait()
        st.session_state.last_ltp = latest_ltp
        ltp_placeholder.metric("NIFTY Future", f"{latest_ltp:.2f}")
    except Empty:
        if 'last_ltp' in st.session_state:
            ltp_placeholder.metric("NIFTY Future", f"{st.session_state.last_ltp:.2f}")

    # Update logs from queue
    log_messages = []
    while not st.session_state.log_queue.empty():
        log_messages.append(st.session_state.log_queue.get_nowait())

    if 'log_history' not in st.session_state:
        st.session_state.log_history = []
    st.session_state.log_history.extend(log_messages)

    log_display = "\\n".join(st.session_state.log_history[-20:]) # Show last 20 logs
    log_placeholder.text_area("Live Logs", log_display, height=300)

    # Update orders display
    if st.session_state.orders:
        orders_df = pd.DataFrame(st.session_state.orders)
        orders_placeholder.dataframe(orders_df)
    else:
        orders_placeholder.info("No trades executed yet.")

# --- Main App ---
if __name__ == "__main__":
    AppState.get_state()

    if st.session_state.logged_in:
        show_dashboard()
        # Auto-refresh the UI to get live updates
        time.sleep(2)
        st.experimental_rerun()
    else:
        show_login_page()
