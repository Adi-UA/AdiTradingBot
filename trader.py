import logging
import os
import time as systime
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.models.bars import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


class Trader:
    """
    Trader class for managing automated trading strategies.

    Args:
        symbol (str): Trading symbol.
        api_key (str): Alpaca API key.
        secret_key (str): Alpaca secret key.
        paper (bool): Use paper trading. Make sure to also use the paper trading API keys.
        max_wait (int): Maximum time to wait for an order to be filled in seconds.
        debug (bool): Enable debug logging
        quick_test (bool): Run the strategy every minute instead of every market day (for testing).
        This will automatically set paper trading to True. So, make sure to use the paper trading API keys.

    Attributes:
        trade_client (TradingClient): Alpaca trading client.
        stock_historical_data_client (StockHistoricalDataClient): Historical data client.
        symbol (str): Trading symbol.
    """

    def __init__(
        self,
        symbol: str = "VOO",
        api_key: str = None,
        secret_key: str = None,
        paper: bool = True,
        max_wait: int = 300,
        debug: bool = False,
        quick_test: bool = False,
    ):
        """
        Initialize the Trader class with required clients and configurations.
        """
        self._init_logging(debug)
        self.api_key = api_key
        self.secret_key = secret_key
        self.quick_test = quick_test
        self.paper = paper if not quick_test else True
        self.trade_client = self._get_trading_client()
        self.stock_historical_data_client = self._get_stock_historical_data_client()
        self.symbol = symbol
        self.max_wait = max_wait

    def _init_logging(self, level) -> None:
        """
        Initialize logging configuration.
        """
        logging.basicConfig(
            level=logging.DEBUG if level else logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    def _validate_account(self, trade_client: TradingClient) -> tuple[bool, str | None]:
        """
        Validate the trading account and configurations.

        Args:
            trade_client (TradingClient): Trading client instance.

        Returns:
            tuple: Validation status and error message if validation fails.
        """
        logging.info("Validating account...")
        account = trade_client.get_account()
        configurations = trade_client.get_account_configurations()
        logging.debug(f"Account information: {account}")

        errors = []
        if account.status != "ACTIVE":
            errors.append("Account is not active. Please check your account status.")

        if not configurations.fractional_trading:
            errors.append("Fractional trading is not enabled. Please enable it.")

        if float(account.cash) < 0:
            errors.append(
                "Balance is negative. Please deposit funds to continue trading."
            )

        if errors:
            error_message = "\n".join([f"- {err}" for err in errors])
            logging.error(f"Account validation failed:\n{error_message}")
            return False, error_message

        logging.info("Account validated successfully.")
        return True, None

    def _get_trading_client(self) -> TradingClient:
        """
        Create and validate the trading client.

        Returns:
            TradingClient: Initialized trading client.
        """
        trade_client = TradingClient(
            api_key=self.api_key, secret_key=self.secret_key, paper=self.paper
        )
        is_valid, error = self._validate_account(trade_client)
        if not is_valid:
            raise RuntimeError(error)

        return trade_client

    def _get_stock_historical_data_client(self) -> StockHistoricalDataClient:
        """
        Create the stock historical data client.

        Returns:
            StockHistoricalDataClient: Historical data client instance.
        """
        return StockHistoricalDataClient(
            api_key=self.api_key, secret_key=self.secret_key
        )

    def _place_order(
        self, qty: float = None, notional: float = None, side: OrderSide = None
    ) -> bool:
        """
        Place an order and monitor its status until filled or timeout.

        Args:
            qty (float): Quantity of shares to trade.
            notional (float): Dollar amount to trade.
            side (OrderSide): BUY or SELL.

        Returns:
            bool: True if order is successfully placed and filled, False otherwise.
        """
        order_type = "NOTIONAL" if notional else "FRACTIONAL"

        request_params = {
            "symbol": self.symbol,
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
        order = self.trade_client.submit_order(request)
        logging.info(f"Order placed: {order}")

        return self._monitor_order(order, qty or notional, order_type, side)

    def _monitor_order(
        self, order, amount: float, trade_type: str, side: OrderSide
    ) -> bool:
        """
        Monitor order status and handle timeouts.

        Args:
            order: Submitted order instance.
            amount (float): Trade amount.
            trade_type (str): Type of trade (NOTIONAL/FRACTIONAL).
            side (OrderSide): BUY or SELL.

        Returns:
            bool: True if order is successfully filled, False otherwise.
        """
        logging.info("Monitoring order status...")
        remaining_time = self.max_wait
        wait_step = 60
        while order.status != "filled" and remaining_time > 0:
            systime.sleep(wait_step)
            remaining_time -= wait_step
            order = self.trade_client.get_order_by_id(order.id)
            logging.info(f"Order status: {order.status}")

        status = "CANCELLED" if order.status != "filled" else "FILLED"
        self._update_trade_log(amount, trade_type, side, status)

        if order.status != "filled":
            self.trade_client.cancel_order_by_id(order.id)

        logging.info(f"Final Order Status: {status}")
        return order.status == "filled"

    def _update_trade_log(
        self, amount: float, trade_type: str, side: OrderSide, status: str
    ) -> None:
        """
        Log trade execution details.

        Args:
            amount (float): Trade amount.
            trade_type (str): Type of trade.
            side (OrderSide): BUY or SELL.
            status (str): Trade status.
        """

        # Make a logs directory
        if not os.path.exists("logs"):
            os.makedirs("logs")

        # Create a trade log file if it doesn't exist
        # Append the trade details to the log file
        log_file = "logs/trade_log.csv"
        if not os.path.exists(log_file):
            with open(log_file, "w") as f:
                f.write("timestamp,symbol,amount,trade_type,side,status\n")

        with open(log_file, "a") as f:
            f.write(
                f"{datetime.now()},{self.symbol},{amount},{trade_type},{side},{status}\n"
            )

    def _fetch_stock_bars(
        self,
        stock_historical_data_client: StockHistoricalDataClient,
        start_date: datetime,
        end_date: datetime,
        timeframe: TimeFrame,
        limit: int,
    ) -> BarSet:
        """
        Fetch historical stock bars.

        Args:
            stock_historical_data_client (StockHistoricalDataClient): Historical data client instance.
            start_date (datetime): Start date for fetching historical data.
            end_date (datetime): End date for fetching historical data.
            timeframe (TimeFrame): Timeframe for fetching historical data.
            limit (int): Number of bars to fetch.

        Returns:
            list: List of historical stock bars. Note: The list may contain fewer bars than the limit if
                  data data  is not available (e.g., weekends).
        """
        request = StockBarsRequest(
            symbol_or_symbols=self.symbol,
            start=start_date,
            end=end_date,
            timeframe=timeframe,
            limit=limit,
        )
        bars = stock_historical_data_client.get_stock_bars(request)
        logging.debug(
            f"{limit} Stock bars fetched: {bars} for {self.symbol} from {start_date} to {end_date}"
        )
        return bars

    def _fetch_stock_closing_prices(self, bars: list) -> list:
        """
        Extract closing prices from historical stock bars in chronological order (oldest to newest from left to right).

        Args:
            bars (list): List of historical stock bars.

        Returns:
            list: List of closing prices.
        """
        # Return the (time, close) tuple for each bar
        data = bars.data[self.symbol]
        closing_prices = [(bar.timestamp, bar.close) for bar in data]
        closing_prices = sorted(closing_prices, key=lambda x: x[0])
        closing_prices = [price for _, price in closing_prices]
        logging.debug(f"Closing prices: {closing_prices}")
        return closing_prices

    def make_decision(
        self, short_prices_5d: list[float], long_prices_20d: list[float]
    ) -> tuple:
        """
        Make a trading decision based on price trends.

        Strategy: Simple Moving Average (SMA) Crossover Strategy (5d and 20d). Buy when the 5d SMA crosses above
        the 20d SMA and sell when the 5d SMA crosses below the 20d SMA.

        Args:
            short_prices_5d (list[float]): List of closing prices for the last 5 days.
            long_prices_20d (list[float]): List of closing prices for the last 20 days.

        Returns:
            tuple: Decision (OrderSide) and multiplier. The multiplier is used to determine the
            trade amount based on the cash or positions available. None means no action.
        """

        sma_5d = np.mean(short_prices_5d)
        sma_20d = np.mean(long_prices_20d)

        if sma_5d > sma_20d:
            return OrderSide.BUY, 0.75
        elif sma_5d < sma_20d:
            return OrderSide.SELL, 0.10
        else:
            return OrderSide.BUY, 0.50

    def run(self) -> None:
        """
        Execute the trading strategy by analyzing prices and executing trades during market hours.
        Strategy executes every 2 days at 9:30 AM ET.
        """
        ny_tz = ZoneInfo("America/New_York")
        while True:
            now = datetime.now(ny_tz)

            if now.weekday() < 5 or self.quick_test:
                if now.time() > time(16, 0) and not self.quick_test:
                    logging.info("Market closed for today. Waiting until next open...")
                    next_open = now + timedelta(days=1)
                    next_open = next_open.replace(hour=9, minute=30, second=0)
                elif now.time() < time(9, 30) and not self.quick_test:
                    logging.info("Market will open soon. Waiting...")
                    next_open = now.replace(hour=9, minute=30, second=0)
                else:
                    logging.info("Market open. Executing trading strategy...")

                    stock_bars_5d = self._fetch_stock_bars(
                        self.stock_historical_data_client,
                        start_date=now - timedelta(days=6),
                        end_date=now - timedelta(days=1),
                        timeframe=TimeFrame.Day,
                        limit=5,
                    )
                    stock_bars_20d = self._fetch_stock_bars(
                        self.stock_historical_data_client,
                        start_date=now - timedelta(days=21),
                        end_date=now - timedelta(days=1),
                        timeframe=TimeFrame.Day,
                        limit=20,
                    )

                    short_prices = self._fetch_stock_closing_prices(stock_bars_5d)
                    long_prices = self._fetch_stock_closing_prices(stock_bars_20d)
                    side, multiplier = self.make_decision(
                        short_prices_5d=short_prices, long_prices_20d=long_prices
                    )
                    logging.debug(f"Decision: {side} - Multiplier: {multiplier}")

                    if side == OrderSide.BUY:
                        cash = float(self.trade_client.get_account().cash)
                        notional = cash * multiplier
                        self._place_order(notional=notional, side=OrderSide.BUY)
                    elif side == OrderSide.SELL:
                        positions = self.trade_client.get_all_positions()
                        symbol_positions = [
                            pos for pos in positions if pos.symbol == self.symbol
                        ]
                        position = symbol_positions[0] if symbol_positions else None
                        if position:
                            qty = float(position.qty) * multiplier
                            self._place_order(qty=qty, side=OrderSide.SELL)
                    else:
                        logging.info("No action taken.")
                    next_open = now + timedelta(days=2)
            else:
                logging.info("Market closed for the week. Waiting until next open...")
                next_open = now + timedelta(days=1)

            sleep_time = (
                (next_open - now).total_seconds() if not self.quick_test else 60
            )
            systime.sleep(sleep_time)
