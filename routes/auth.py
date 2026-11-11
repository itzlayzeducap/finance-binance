import crypto

from functools import wraps
from db import Database
from helpers import error

import sqlite3
from flask import Blueprint, g, redirect, render_template, request

bp = Blueprint("auth", __name__, url_prefix="/auth")
db = Database()


def get_token(request) -> str | None:
    return request.cookies.get("token") or request.headers.get("Authorization")


async def is_authorized(request):
    token = get_token(request)
    if not token:
        return None

    return await db.get_user_id(token)


def login_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        user_id = await is_authorized(request)
        if not user_id:
            return redirect("/auth/login")

        g.user_id = user_id
        return await func(*args, **kwargs)

    return wrapper


def start_session(token: str):
    response = redirect("/")
    response.set_cookie(
        "token", token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax"
    )

    return response


@bp.route("/register", methods=["GET", "POST"])
async def register():
    if request.method == "GET":
        return render_template("register.html")

    form = request.form

    username = form.get("username", "").strip()
    password = form.get("password")

    confirmation = form.get("confirmation")

    if not username or not password:
        return error("register.html", "юзернейм или пароль не введены")

    if len(username) > 32:
        return error("register.html", "юзернейм слишком длинный")

    if len(password.encode()) > 72:
        return error("register.html", "пароль слишком длинный")

    if password != confirmation:
        return error("register.html", "пароли не совпадают")

    hashed = crypto.hash_password(password)

    try:
        user_id = await db.register(username, hashed)
    except sqlite3.IntegrityError:
        return error("register.html", "такой юзернейм уже занят")

    return start_session(await db.create_token(user_id))


@bp.route("/login", methods=["GET", "POST"])
async def login():
    if request.method == "GET":
        return render_template("login.html")

    form = request.form

    username = form.get("username", "").strip()
    password = form.get("password")

    if not username or not password:
        return error("login.html", "юзернейм или пароль не введены")

    token = await db.login(username, password)

    if not token:
        return error("login.html", "неверный юзернейм или пароль")

    return start_session(token)


@bp.post("/logout")
async def logout():
    user_id = await is_authorized(request)
    if user_id:
        await db.logout(user_id, get_token(request))

    response = redirect("/auth/login")
    response.delete_cookie("token")

    return response


@bp.route("/password", methods=["GET", "POST"])
@login_required
async def password():
    current = await db.get_session_id(get_token(request))
    sessions = await db.list_sessions(g.user_id)

    if request.method == "GET":
        return render_template("account.html", sessions=sessions, current=current)

    form = request.form

    old = form.get("old")
    new = form.get("new")

    confirmation = form.get("confirmation")

    if not old or not new:
        return error(
            "account.html", "заполни все поля", sessions=sessions, current=current
        )

    if len(new.encode()) > 72:
        return error(
            "account.html", "пароль слишком длинный", sessions=sessions, current=current
        )

    if new != confirmation:
        return error(
            "account.html", "пароли не совпадают", sessions=sessions, current=current
        )

    user = await db.get_user_by_id(g.user_id)
    if not crypto.check_password(old, user["hash"]):
        return error(
            "account.html", "старый пароль неверный", sessions=sessions, current=current
        )

    await db.set_password(g.user_id, crypto.hash_password(new), get_token(request))

    # остальные сессии удалились, список надо взять заново
    sessions = await db.list_sessions(g.user_id)
    return render_template(
        "account.html", success="пароль изменён", sessions=sessions, current=current
    )


@bp.post("/sessions/<int:session_id>/end")
@login_required
async def end_session(session_id: int):
    current = await db.get_session_id(get_token(request))

    await db.end_session(g.user_id, session_id)

    if session_id == current:
        response = redirect("/auth/login")
        response.delete_cookie("token")

        return response

    return redirect("/auth/password")
