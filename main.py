from datetime import datetime, timedelta, time
import time as systime
import os
import random
from zoneinfo import ZoneInfo

import logging
from config import (
    ALPACA_API_KEY_ID,
    ALPACA_API_SECRET_KEY,
    PAPER,
    PAPER_ALPACA_API_KEY_ID,
    PAPER_ALPACA_API_SECRET_KEY,
    LOG_LEVEL,
    MIN_CASH,
    SYMBOL,
    MAX_WAIT,
)

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, OrderType, QueryOrderStatus, TimeInForce


def init_logging():
    """Initialize logging configuration."""
    logging.basicConfig(
        level=LOG_LEVEL, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def validate_account(trade_client):
    """Validate the trading account and configurations."""
    logging.debug("Validating account...")
    account = trade_client.get_account()
    configurations = trade_client.get_account_configurations()
    logging.debug(f"Account information: {account}")

    errors = []
    if account.status != "ACTIVE":
        errors.append("Account is not active. Please check your account status.")

    if not configurations.fractional_trading:
        errors.append("Fractional trading is not enabled. Please enable it.")

    if float(account.cash) < MIN_CASH:
        errors.append("Insufficient cash to trade. Please deposit more funds.")

    if errors:
        error_message = "\n".join([f"- {err}" for err in errors])
        logging.error(f"Account validation failed:\n{error_message}")
        return False, error_message

    logging.debug("Account validated successfully.")
    return True, None


def get_trading_client():
    """Create and validate the trading client."""
    api_key = ALPACA_API_KEY_ID if not PAPER else PAPER_ALPACA_API_KEY_ID
    secret_key = ALPACA_API_SECRET_KEY if not PAPER else PAPER_ALPACA_API_SECRET_KEY

    trade_client = TradingClient(api_key=api_key, secret_key=secret_key, paper=PAPER)
    is_valid, error = validate_account(trade_client)
    if not is_valid:
        raise RuntimeError(error)

    return trade_client


def place_order(trade_client, symbol, qty=None, notional=None, side=OrderSide.BUY):
    """Place an order and monitor its status until filled or timeout."""
    order_type = "NOTIONAL" if notional else "FRACTIONAL"

    request_params = {
        "symbol": symbol,
        "side": side,
        "type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
    }

    if notional:
        request_params["notional"] = notional
    elif qty:
        request_params["qty"] = qty
    else:
        raise ValueError("Either 'qty' or 'notional' must be provided.")

    request = MarketOrderRequest(**request_params)
    order = trade_client.submit_order(request)
    logging.debug(f"Order placed: {order}")

    # Monitor the order status
    return monitor_order(trade_client, order, symbol, qty or notional, order_type, side)


def monitor_order(trade_client, order, symbol, amount, trade_type, side):
    """Monitor order status and handle timeouts."""
    remaining_time = MAX_WAIT
    while order.status != "filled" and remaining_time > 0:
        systime.sleep(60)
        remaining_time -= 60
        order = trade_client.get_order_by_id(order.id)
        logging.debug(f"Order status: {order.status}")

    if order.status != "filled":
        trade_client.cancel_order_by_id(order.id)
        update_trade_log(symbol, amount, trade_type, side, "CANCELLED")
        logging.warning("Order not filled. Cancelled.")
        return None

    update_trade_log(symbol, amount, trade_type, side, "FILLED")
    return order


def update_trade_log(symbol, amount, trade_type, side, status):
    """Log trade execution details."""
    log_file = "trade_log.csv"
    if not os.path.exists(log_file):
        with open(log_file, "w") as f:
            f.write("time,symbol,notional,trade_type,side,status\n")

    with open(log_file, "a") as f:
        f.write(f"{datetime.now()},{symbol},{amount},{trade_type},{side},{status}\n")


def execute_daily_trade_strategy(trade_client, symbol):
    """Execute a basic daily trade strategy."""
    trade_client.cancel_orders()
    logging.info("Canceled all open orders.")

    strategy = OrderSide.BUY if random.choice([True, False]) else OrderSide.SELL
    if strategy == OrderSide.BUY:
        cash = float(trade_client.get_account().cash)
        notional = cash * 0.4
        place_order(trade_client, symbol, notional=notional, side=OrderSide.BUY)
    else:
        position = trade_client.get_position(symbol)
        if position:
            qty = float(position.qty) * 0.3
            place_order(trade_client, symbol, qty=qty, side=OrderSide.SELL)


def run_trade_loop(trade_client, symbol):
    """Main trading loop to execute trades during market hours."""
    ny_tz = ZoneInfo("America/New_York")

    while True:
        now = datetime.now(ny_tz)
        if now.weekday() < 5:
            if time(9, 30) <= now.time() <= time(16, 0):
                execute_daily_trade_strategy(trade_client, symbol)
            elif now.time() < time(9, 30):
                time_to_open = now.replace(hour=9, minute=30, second=0) - now
                time_to_open_hours = time_to_open.total_seconds() // 3600
                logging.debug(
                    f"Market closed. Waiting for opening: {time_to_open_hours} hours."
                )
                systime.sleep(time_to_open.total_seconds())

        else:
            next_open = now + timedelta(days=1)
            next_open = next_open.replace(hour=9, minute=30, second=0)
            time_to_wait = next_open - now
            time_to_wait_hours = time_to_wait.total_seconds() // 3600
            logging.debug(
                f"Market closed. Waiting for next open: {time_to_wait_hours} hours."
            )
            systime.sleep(time_to_wait.total_seconds())


def main():
    """Initialize and run the trading bot."""
    init_logging()
    trade_client = get_trading_client()
    run_trade_loop(trade_client, SYMBOL)


if __name__ == "__main__":
    main()
