from exchange.pexchange import ccxt, ccxt_async, httpx
from devtools import debug
from exchange.model import MarketOrder
import exchange.error as error


class Binance:
    def __init__(self, key, secret):
        self.client = ccxt.binance(
            {
                "apiKey": key,
                "secret": secret,
                "options": {"adjustForTimeDifference": True},
            }
        )
        self.client.load_markets()
        self.position_mode = "one-way"
        self.order_info: MarketOrder = None

    def init_info(self, order_info: MarketOrder):
        self.order_info = order_info

        unified_symbol = order_info.unified_symbol
        market = self.client.market(unified_symbol)

        if order_info.amount is not None:
            order_info.amount = float(
                self.client.amount_to_precision(
                    order_info.unified_symbol, order_info.amount
                )
            )

        if order_info.is_futures:
            if order_info.is_coinm:
                is_contract = market.get("contract")
                if is_contract:
                    order_info.is_contract = True
                    order_info.contract_size = market.get("contractSize")
                self.client.options["defaultType"] = "delivery"
            else:
                self.client.options["defaultType"] = "swap"
        else:
            self.client.options["defaultType"] = "spot"

    def get_ticker(self, symbol: str):
        return self.client.fetch_ticker(symbol)

    def get_price(self, symbol: str):
        return self.get_ticker(symbol)["last"]

    def get_futures_position(self, symbol=None, all=False):
        if symbol is None and all:
            positions = self.client.fetch_balance()["info"]["positions"]
            positions = [
                position
                for position in positions
                if float(position["positionAmt"]) != 0
            ]
            return positions

        positions = None
        if self.order_info.is_coinm:
            positions = self.client.fetch_balance()["info"]["positions"]
            positions = [
                position
                for position in positions
                if float(position["positionAmt"]) != 0
                and position["symbol"] == self.client.market(symbol).get("id")
            ]
        else:
            positions = self.client.fetch_positions(symbols=[symbol])

        long_contracts = None
        short_contracts = None
        if positions:
            if self.order_info.is_coinm:
                for position in positions:
                    amt = float(position["positionAmt"])
                    if position["positionSide"] == "LONG":
                        long_contracts = amt
                    elif position["positionSide"] == "SHORT":
                        short_contracts: float = amt
                    elif position["positionSide"] == "BOTH":
                        if amt > 0:
                            long_contracts = amt
                        elif amt < 0:
                            short_contracts = abs(amt)
            else:
                for position in positions:
                    if position["side"] == "long":
                        long_contracts = position["contracts"]
                    elif position["side"] == "short":
                        short_contracts = position["contracts"]
            if self.order_info.is_close and self.order_info.is_buy:
                if not short_contracts:
                    raise error.ShortPositionNoneError()
                else:
                    return short_contracts
            elif self.order_info.is_close and self.order_info.is_sell:
                if not long_contracts:
                    raise error.LongPositionNoneError()
                else:
                    return long_contracts
        else:
            raise error.PositionNoneError()

    def get_balance(self, base: str):
        free_balance_by_base = None

        if self.order_info.is_entry or (
            self.order_info.is_spot
            and (self.order_info.is_buy or self.order_info.is_sell)
        ):
            free_balance = (
                self.client.fetch_free_balance()
                if not self.order_info.is_total
                else self.client.fetch_total_balance()
            )
            free_balance_by_base = free_balance.get(base)

        if free_balance_by_base is None or free_balance_by_base == 0:
            raise error.FreeAmountNoneError()
        return free_balance_by_base

    def get_amount(self, order_info: MarketOrder) -> float:
        if order_info.amount is not None and order_info.percent is not None:
            raise error.AmountPercentBothError()
        elif order_info.amount is not None:
            if order_info.is_contract:
                current_price = self.get_price(order_info.unified_symbol)
                result = (order_info.amount * current_price) // order_info.contract_size
            else:
                result = order_info.amount
        elif order_info.percent is not None:
            if order_info.is_entry or (order_info.is_spot and order_info.is_buy):
                if order_info.is_coinm:
                    free_base = self.get_balance(order_info.base)
                    if order_info.is_contract:
                        current_price = self.get_price(order_info.unified_symbol)
                        result = (
                            free_base * order_info.percent / 100 * current_price
                        ) // order_info.contract_size
                    else:
                        result = free_base * order_info.percent / 100
                else:
                    free_quote = self.get_balance(order_info.quote)
                    cash = free_quote * (order_info.percent - 0.5) / 100
                    current_price = self.get_price(order_info.unified_symbol)
                    if order_info.is_contract:
                        result = (cash / current_price) // order_info.contract_size
                    else:
                        result = cash / current_price
            elif self.order_info.is_close:
                if order_info.is_contract:
                    free_amount = self.get_futures_position(order_info.unified_symbol)
                    result = free_amount * order_info.percent / 100
                else:
                    free_amount = self.get_futures_position(order_info.unified_symbol)
                    result = free_amount * float(order_info.percent) / 100
            elif order_info.is_spot and order_info.is_sell:
                free_amount = self.get_balance(order_info.base)
                result = free_amount * float(order_info.percent) / 100

            result = float(
                self.client.amount_to_precision(order_info.unified_symbol, result)
            )
            order_info.amount_by_percent = result
        else:
            raise error.AmountPercentNoneError()

        return result

    def set_leverage(self, leverage, symbol):
        if self.order_info.is_futures:
            self.client.set_leverage(leverage, symbol)

    # 시장가 주문 처리
    def market_order(self, order_info: MarketOrder):
        from exchange.pexchange import retry

        symbol = order_info.unified_symbol
        params = {}
        try:
            return retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                order_info.amount,
                None,
                params,
                order_info=order_info,
                max_attempts=5,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    # 지정가 주문 처리
    def limit_order(self, order_info: MarketOrder):
        from exchange.pexchange import retry

        symbol = order_info.unified_symbol
        params = {}
        try:
            return retry(
                self.client.create_order,
                symbol,
                "limit",
                order_info.side,
                order_info.amount,
                order_info.price,  # 지정가 주문에서 가격 필요
                params,
                order_info=order_info,
                max_attempts=5,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    def market_buy(self, order_info: MarketOrder):
        # 수량 계산
        buy_amount = self.get_amount(order_info)
        order_info.amount = buy_amount
        return self.market_order(order_info)

    def market_sell(self, order_info: MarketOrder):
        sell_amount = self.get_amount(order_info)
        order_info.amount = sell_amount
        return self.market_order(order_info)

    def limit_buy(self, order_info: MarketOrder):
        buy_amount = self.get_amount(order_info)
        order_info.amount = buy_amount
        return self.limit_order(order_info)

    def limit_sell(self, order_info: MarketOrder):
        sell_amount = self.get_amount(order_info)
        order_info.amount = sell_amount
        return self.limit_order(order_info)

    def market_entry(self, order_info: MarketOrder):
        from exchange.pexchange import retry

        symbol = self.order_info.unified_symbol
        entry_amount = self.get_amount(order_info)
        if entry_amount == 0:
            raise error.MinAmountError()
        params = {}
        if order_info.leverage is not None:
            self.set_leverage(order_info.leverage, symbol)

        try:
            result = retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(entry_amount),
                None,
                params,
                order_info=order_info,
                max_attempts=10,
                delay=0.1,
                instance=self,
            )
            return result
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    def market_close(self, order_info: MarketOrder):
        from exchange.pexchange import retry

        symbol = self.order_info.unified_symbol
        close_amount = self.get_amount(order_info)
        params = {"reduceOnly": True}

        try:
            return retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(close_amount),
                None,
                params,
                order_info=order_info,
                max_attempts=10,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    def get_listen_key(self):
        url = "https://fapi.binance.com/fapi/v1/listenKey"
        listenkey = httpx.post(
            url, headers={"X-MBX-APIKEY": self.client.apiKey}
        ).json()["listenKey"]
        return listenkey

    def get_trades(self):
        is_futures = self.order_info.is_futures
        if is_futures:
            trades = self.client.fetch_my_trades()
            print(trades)
