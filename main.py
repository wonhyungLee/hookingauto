from fastapi import FastAPI, BackgroundTasks, Request, status
from fastapi.responses import ORJSONResponse
from fastapi.exceptions import RequestValidationError
import httpx
from exchange.stock.kis import KoreaInvestment
from exchange.model import MarketOrder, PriceRequest, HedgeData, OrderRequest
from exchange.utility import (
    log_order_message,
    log_alert_message,
    log_order_error_message,
    log_validation_error_message,
    log_error_message,
    log_message,
)
import traceback
from exchange import get_exchange, log_message, db, get_bot, pocket
import os

VERSION = "0.1.3"
app = FastAPI(default_response_class=ORJSONResponse)


def get_error(e):
    tb = traceback.extract_tb(e.__traceback__)
    error_msg = [f"File {tb_info.filename}, line {tb_info.lineno}, in {tb_info.name}" for tb_info in tb]
    error_msg.append(str(e))
    return error_msg


@app.on_event("startup")
async def startup():
    log_message(f"POABOT 실행 완료! - 버전:{VERSION}")


@app.on_event("shutdown")
async def shutdown():
    db.close()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    msgs = [f"[에러{index+1}] {error.get('msg')} \n{error.get('loc')}" for index, error in enumerate(exc.errors())]
    message = "[Error]\n" + "\n".join(msgs)
    log_validation_error_message(f"{message}\n {exc.body}")
    return await request_validation_exception_handler(request, exc)


@app.get("/ip")
async def get_ip():
    data = httpx.get("https://ipv4.jsonip.com").json()["ip"]
    log_message(data)


@app.get("/hi")
async def welcome():
    return "hi!!"


@app.post("/price")
async def price(price_req: PriceRequest, background_tasks: BackgroundTasks):
    try:
        exchange = get_exchange(price_req.exchange)
        price = exchange.dict()[price_req.exchange].fetch_price(price_req.base, price_req.quote)
        return price
    except Exception as e:
        error_msg = get_error(e)
        background_tasks.add_task(log_error_message, "\n".join(error_msg), price_req)
        return {"result": "error", "message": str(e)}


def process_market_order(bot, order_info):
    if order_info.is_entry:
        return bot.market_entry(order_info)
    elif order_info.is_close:
        return bot.market_close(order_info)
    elif order_info.is_buy:
        return bot.market_buy(order_info)
    elif order_info.is_sell:
        return bot.market_sell(order_info)


def process_limit_order(bot, order_info):
    if order_info.is_entry:
        return bot.limit_entry(order_info)
    elif order_info.is_close:
        return bot.limit_close(order_info)
    elif order_info.is_buy:
        return bot.limit_buy(order_info)
    elif order_info.is_sell:
        return bot.limit_sell(order_info)


@app.post("/order")
async def order(order_info: MarketOrder, background_tasks: BackgroundTasks):
    try:
        bot = get_bot(order_info.exchange, order_info.kis_number)
        bot.init_info(order_info)

        if order_info.type == 'market':
            order_result = process_market_order(bot, order_info)
        elif order_info.type == 'limit':
            order_result = process_limit_order(bot, order_info)
        else:
            raise ValueError("Unsupported order type")

        background_tasks.add_task(log_order_message, order_info.exchange, order_result, order_info)

    except Exception as e:
        error_msg = get_error(e)
        background_tasks.add_task(log_error_message, "\n".join(error_msg), order_info)
        return {"result": "error", "message": str(e)}

    return {"result": "success"}


def get_hedge_records(base):
    records = pocket.get_full_list("kimp", query_params={"filter": f'base = "{base}"'})
    binance_amount = 0.0
    binance_records_id = []
    upbit_amount = 0.0
    upbit_records_id = []
    for record in records:
        if record.exchange == "BINANCE":
            binance_amount += record.amount
            binance_records_id.append(record.id)
        elif record.exchange == "UPBIT":
            upbit_amount += record.amount
            upbit_records_id.append(record.id)

    return {
        "BINANCE": {"amount": binance_amount, "records_id": binance_records_id},
        "UPBIT": {"amount": upbit_amount, "records_id": upbit_records_id},
    }


@app.post("/hedge")
async def hedge(hedge_data: HedgeData, background_tasks: BackgroundTasks):
    exchange_name = hedge_data.exchange.upper()
    bot = get_bot(exchange_name)
    upbit = get_bot("UPBIT")

    base = hedge_data.base
    quote = hedge_data.quote
    amount = hedge_data.amount
    leverage = hedge_data.leverage
    hedge = hedge_data.hedge

    foreign_order_info = OrderRequest(
        exchange=exchange_name,
        base=base,
        quote=quote,
        side="entry/sell",
        type="market",
        amount=amount,
        leverage=leverage,
    )
    bot.init_info(foreign_order_info)
    
    if hedge == "ON":
        try:
            if amount is None:
                raise Exception("헷지할 수량을 요청하세요")
            binance_order_result = bot.market_entry(foreign_order_info)
            binance_order_amount = binance_order_result["amount"]
            pocket.create(
                "kimp",
                {
                    "exchange": "BINANCE",
                    "base": base,
                    "quote": quote,
                    "amount": binance_order_amount,
                },
            )
            if leverage is None:
                leverage = 1
            try:
                korea_order_info = OrderRequest(
                    exchange="UPBIT",
                    base=base,
                    quote="KRW",
                    side="buy",
                    type="market",
                    amount=binance_order_amount,
                )
                upbit.init_info(korea_order_info)
                upbit_order_result = upbit.market_buy(korea_order_info)
            except Exception as e:
                hedge_records = get_hedge_records(base)
                binance_records_id = hedge_records["BINANCE"]["records_id"]
                binance_amount = hedge_records["BINANCE"]["amount"]
                binance_order_result = bot.market_close(
                    OrderRequest(
                        exchange=exchange_name,
                        base=base,
                        quote=quote,
                        side="close/buy",
                        amount=binance_amount,
                    )
                )
                for binance_record_id in binance_records_id:
                    pocket.delete("kimp", binance_record_id)
                log_message("[헷지 실패] 업비트에서 에러가 발생하여 바이낸스 포지션을 종료합니다")
            else:
                upbit_order_info = upbit.get_order(upbit_order_result["id"])
                upbit_order_amount = upbit_order_info["filled"]
                pocket.create(
                    "kimp",
                    {
                        "exchange": "UPBIT",
                        "base": base,
                        "quote": "KRW",
                        "amount": upbit_order_amount,
                    },
                )
                log_hedge_message(
                    exchange_name,
                    base,
                    quote,
                    binance_order_amount,
                    upbit_order_amount,
                    hedge,
                )
        except Exception as e:
            background_tasks.add_task(log_error_message, traceback.format_exc(), "헷지 에러")
            return {"result": "error"}
        else:
            return {"result": "success"}

    elif hedge == "OFF":
        try:
            records = pocket.get_full_list("kimp", query_params={"filter": f'base = "{base}"'})
            binance_amount = 0.0
            binance_records_id = []
            upbit_amount = 0.0
            upbit_records_id = []
            for record in records:
                if record.exchange == "BINANCE":
                    binance_amount += record.amount
                    binance_records_id.append(record.id)
                elif record.exchange == "UPBIT":
                    upbit_amount += record.amount
                    upbit_records_id.append(record.id)

            if binance_amount > 0 and upbit_amount > 0:
                order_info = OrderRequest(
                    exchange="BINANCE",
                    base=base,
                    quote=quote,
                    side="close/buy",
                    amount=binance_amount,
                )
                binance_order_result = bot.market_close(order_info)
                for binance_record_id in binance_records_id:
                    pocket.delete("kimp", binance_record_id)

                order_info = OrderRequest(
                    exchange="UPBIT",
                    base=base,
                    quote="KRW",
                    side="sell",
                    amount=upbit_amount,
                )
                upbit_order_result = upbit.market_sell(order_info)
                for upbit_record_id in upbit_records_id:
                    pocket.delete("kimp", upbit_record_id)

                log_hedge_message(exchange_name, base, quote, binance_amount, upbit_amount, hedge)
            elif binance_amount == 0 and upbit_amount == 0:
                log_message(f"{exchange_name}, UPBIT에 종료할 수량이 없습니다")
            elif binance_amount == 0:
                log_message(f"{exchange_name}에 종료할 수량이 없습니다")
            elif upbit_amount == 0:
                log_message("UPBIT에 종료할 수량이 없습니다")
        except Exception as e:
            background_tasks.add_task(log_error_message, traceback.format_exc(), "헷지종료 에러")
            return {"result": "error"}
        else:
            return {"result": "success"}
