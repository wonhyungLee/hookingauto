def log_order_message(exchange_name, order_result: dict, order_info: MarketOrder):
    date = parse_time(datetime.utcnow().timestamp())
    if not order_info.is_futures and order_info.is_buy and exchange_name in COST_BASED_ORDER_EXCHANGES:
        f_name = "비용"
        if order_info.amount is not None:
            if exchange_name == "UPBIT":
                amount = str(order_result.get("cost"))
            elif exchange_name == "BITGET":
                amount = str(order_info.amount * order_info.price)
            elif exchange_name == "BYBIT":
                amount = str(order_result.get("info").get("orderQty"))
        elif order_info.percent is not None:
            f_name = "비율"
            amount = f"{order_info.percent}%"
    else:
        f_name = "수량"
        amount = None
        if exchange_name in ("KRX", "NASDAQ", "AMEX", "NYSE"):
            if order_info.amount is not None:
                amount = str(order_info.amount)
            elif order_info.percent is not None:
                f_name = "비율"
                amount = f"{order_info.percent}%"
        elif order_result.get("amount") is None:
            if order_info.amount is not None:
                if exchange_name == "OKX":
                    if order_info.is_futures:
                        f_name = "계약(수량)"
                        amount = f"{order_info.amount // order_info.contract_size}({order_info.contract_size * (order_info.amount // order_info.contract_size)})"
                    else:
                        amount = f"{order_info.amount}"
                else:
                    amount = str(order_info.amount)
            elif order_info.percent is not None:
                if order_info.amount_by_percent is not None:
                    f_name = "비율(수량)" if order_info.is_contract is None else "비율(계약)"
                    amount = f"{order_info.percent}%({order_info.amount_by_percent})"
                else:
                    f_name = "비율"
                    amount = f"{order_info.percent}%"
        elif order_result.get("amount") is not None:
            if order_info.contract_size is not None:
                f_name = "계약"
                if order_result.get("cost") is not None:
                    f_name = "계약(비용)"
                    amount = f"{order_result.get('amount')}({order_result.get('cost'):.2f})"
                else:
                    amount = f"{order_result.get('amount')}"
            else:
                if order_info.amount is not None:
                    f_name = "수량"
                    amount = f"{order_result.get('amount')}"
                elif order_info.percent is not None:
                    f_name = "비율(수량)" if order_info.is_contract is None else "비율(계약)"
                    amount = f"{order_info.percent}%({order_result.get('amount')})"

    # 추가: 지정가 주문인 경우 price(가격) 정보 포함
    if order_info.type == "limit" and order_info.price:
        f_name = "지정가(가격)"
        price = f"{order_info.price}"
    else:
        price = order_result.get("price", "N/A")

    symbol = f"{order_info.base}/{order_info.quote+'.P' if order_info.is_crypto and order_info.is_futures else order_info.quote}"

    side = ""
    if order_info.is_futures:
        if order_info.is_entry:
            if order_info.is_buy:
                side = "롱 진입"
            elif order_info.is_sell:
                side = "숏 진입"
        elif order_info.is_close:
            if order_info.is_buy:
                side = "숏 종료"
            elif order_info.is_sell:
                side = "롱 종료"
    else:
        if order_info.is_buy:
            side = "매수"
        elif order_info.is_sell:
            side = "매도"

    # 지정가 주문일 때 가격 필드를 로그에 포함
    content = f"일시\n{date}\n\n거래소\n{exchange_name}\n\n심볼\n{symbol}\n\n거래유형\n{side}\n\n{amount}\n\n가격\n{price}"
    embed = Embed(
        title=order_info.order_name,
        description=f"체결: {exchange_name} {symbol} {side} {amount} 가격: {price}",
        color=0x0000FF,
    )
    embed.add_field(name="일시", value=str(date), inline=False)
    embed.add_field(name="거래소", value=exchange_name, inline=False)
    embed.add_field(name="심볼", value=symbol, inline=False)
    embed.add_field(name="거래유형", value=side, inline=False)
    if amount:
        embed.add_field(name=f_name, value=amount, inline=False)
    if price:
        embed.add_field(name="가격", value=price, inline=False)
    if order_info.leverage is not None:
        embed.add_field(name="레버리지", value=f"{order_info.leverage}배", inline=False)
    log_message(content, embed)


def log_order_error_message(error: str | Exception, order_info: MarketOrder):
    if isinstance(error, Exception):
        error = get_error(error)

    if order_info is not None:
        # discord
        embed = Embed(
            title=order_info.order_name,
            description=f"[주문 오류가 발생했습니다]\n{error}",
            color=0xFF0000,
        )
        if order_info.type == "limit":
            embed.add_field(name="주문 유형", value="지정가 주문", inline=False)
            embed.add_field(name="지정가", value=f"{order_info.price}", inline=False)
        log_message(embed=embed)

        # logger
        logger.error(f"[주문 오류가 발생했습니다]\n{error}")
    else:
        # discord
        embed = Embed(
            title="오류",
            description=f"[오류가 발생했습니다]\n{error}",
            color=0xFF0000,
        )
        log_message(embed=embed)

        # logger
        logger.error(f"[오류가 발생했습니다]\n{error}")
