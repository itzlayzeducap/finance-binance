from flask import Blueprint, g, render_template, request, redirect

from helpers import error, lookup, lookup_many
from routes.auth import db, login_required

bp = Blueprint("finance", __name__)


def parse_shares(value: str | None) -> int | None:
    if not value or not value.isdecimal() or int(value) < 1:
        return None

    return int(value)


@bp.get("/")
@login_required
async def index():
    holdings = await db.portfolio(g.user_id)

    quotes = await lookup_many([row["symbol"] for row in holdings])

    stocks = []
    cash = await db.balance(g.user_id)
    total = cash

    for row, quote in zip(holdings, quotes):
        price = quote["price"] if quote else None
        value = price * row["shares"] if quote else 0

        stocks.append(
            {
                "symbol": row["symbol"],
                "name": quote["name"] if quote else "",
                "shares": row["shares"],
                "price": price,
                "value": value,
            }
        )
        total += value

    return render_template("index.html", stocks=stocks, cash=cash, total=total)


@bp.route("/quote", methods=["GET", "POST"])
@login_required
async def quote():
    if request.method == "GET":
        return render_template("quote.html")

    symbol = request.form.get("symbol", "")
    result = await lookup(symbol)

    if not result:
        return error("quote.html", "акция не найдена")

    return render_template("quote.html", result=result)


@bp.route("/buy", methods=["GET", "POST"])
@login_required
async def buy():
    if request.method == "GET":
        return render_template("buy.html", symbol=request.args.get("symbol", ""))

    symbol = request.form.get("symbol", "")
    shares = parse_shares(request.form.get("shares"))

    if shares is None:
        return error(
            "buy.html", "количество должно быть целым числом больше 0", symbol=symbol
        )

    result = await lookup(symbol)
    if not result:
        return error("buy.html", "акция не найдена", symbol=symbol)

    try:
        await db.buy(g.user_id, result["symbol"], shares, result["price"])
    except ValueError as e:
        return error("buy.html", str(e), symbol=symbol)

    return redirect("/")


@bp.route("/sell", methods=["GET", "POST"])
@login_required
async def sell():
    holdings = await db.portfolio(g.user_id)

    if request.method == "GET":
        return render_template("sell.html", holdings=holdings)

    symbol = request.form.get("symbol", "")
    shares = parse_shares(request.form.get("shares"))

    if not symbol:
        return error("sell.html", "выбери акцию", holdings=holdings)

    if shares is None:
        return error(
            "sell.html",
            "количество должно быть целым числом больше 0",
            holdings=holdings,
        )

    result = await lookup(symbol)
    if not result:
        return error(
            "sell.html", "не получилось узнать цену, попробуй позже", holdings=holdings
        )

    try:
        await db.sell(g.user_id, result["symbol"], shares, result["price"])
    except ValueError as e:
        return error("sell.html", str(e), holdings=holdings)

    return redirect("/")


@bp.get("/history")
@login_required
async def history():
    return render_template("history.html", rows=await db.history(g.user_id))
