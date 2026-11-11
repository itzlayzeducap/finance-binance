import asyncio
import sqlite3

import aiosqlite

import crypto


class Connection:
    def __init__(self, name: str, transaction: bool = False):
        self.name = name
        self.transaction = transaction

    async def __aenter__(self):
        self.db = await aiosqlite.connect(self.name, isolation_level=None)
        self.db.row_factory = sqlite3.Row

        if self.transaction:
            try:
                await self.db.execute("BEGIN IMMEDIATE")
            except BaseException:
                await self.db.close()
                raise

        return self.db

    async def __aexit__(self, exc_type, __, _):
        try:
            if self.transaction:
                if exc_type:
                    await self.db.rollback()
                else:
                    await self.db.commit()
        finally:
            await self.db.close()


class Database:
    def __init__(self, name: str = "finance.db"):
        self.name = name
        asyncio.run(self.create_tables())

    def connect(self):
        return Connection(self.name)

    def transaction(self):
        return Connection(self.name, transaction=True)

    async def create_tables(self):
        async with self.connect() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    hash TEXT NOT NULL,
                    cash REAL DEFAULT 10000
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    price REAL NOT NULL,
                    type TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)

    async def execute(self, query, params=()):
        async with self.connect() as db:
            cursor = await db.execute(query, params)
            return cursor.lastrowid

    async def fetchone(self, query, params=()):
        async with self.connect() as db:
            cursor = await db.execute(query, params)
            return await cursor.fetchone()

    async def fetchall(self, query, params=()):
        async with self.connect() as db:
            cursor = await db.execute(query, params)
            return await cursor.fetchall()

    # USERS

    async def register(self, username: str, password_hash: str):
        return await self.execute(
            "INSERT INTO users (username, hash) VALUES (?, ?)",
            (username, password_hash),
        )

    async def create_token(self, user_id: int) -> str:
        token, token_hash = crypto.create_token()

        await self.execute(
            "INSERT INTO tokens (user_id, token_hash) VALUES (?, ?)",
            (user_id, token_hash),
        )

        return token

    async def login(self, username: str, password: str) -> str | None:
        user = await self.get_user(username)

        if not user or not crypto.check_password(password, user["hash"]):
            return None

        return await self.create_token(user["id"])

    async def get_user(self, username: str):
        return await self.fetchone(
            "SELECT * FROM users WHERE username = ?", (username,)
        )

    async def get_user_by_id(self, user_id: int):
        return await self.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    async def get_user_id(self, token: str) -> int | None:
        row = await self.fetchone(
            """
            SELECT user_id FROM tokens
            WHERE token_hash = ? AND timestamp > datetime('now', '-30 days')
            """,
            (crypto.hash_token(token),),
        )

        return row["user_id"] if row else None

    async def get_session_id(self, token: str) -> int | None:
        row = await self.fetchone(
            "SELECT id FROM tokens WHERE token_hash = ?", (crypto.hash_token(token),)
        )

        return row["id"] if row else None

    async def list_sessions(self, user_id: int):
        return await self.fetchall(
            "SELECT id, timestamp FROM tokens WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )

    async def end_session(self, user_id: int, session_id: int):
        await self.execute(
            "DELETE FROM tokens WHERE user_id = ? AND id = ?", (user_id, session_id)
        )

    async def logout(self, user_id: int, token: str):
        await self.execute(
            "DELETE FROM tokens WHERE user_id = ? AND token_hash = ?",
            (user_id, crypto.hash_token(token)),
        )

    async def set_password(self, user_id: int, password_hash: str, keep_token: str):
        async with self.transaction() as db:
            await db.execute(
                "UPDATE users SET hash = ? WHERE id = ?", (password_hash, user_id)
            )
            await db.execute(
                "DELETE FROM tokens WHERE user_id = ? AND token_hash != ?",
                (user_id, crypto.hash_token(keep_token)),
            )

    # BALANCE

    async def balance(self, user_id: int):
        row = await self.fetchone("SELECT cash FROM users WHERE id = ?", (user_id,))
        return row["cash"]

    # BUY / SELL

    async def buy(self, user_id: int, symbol: str, shares: int, price: float):
        total = round(shares * price, 2)

        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE users SET cash = cash - ? WHERE id = ? AND cash >= ?",
                (total, user_id, total),
            )

            if cursor.rowcount == 0:
                raise ValueError("не хватает денег")

            await self._add_transaction(db, user_id, symbol, shares, price, "buy")

    async def sell(self, user_id: int, symbol: str, shares: int, price: float):
        total = round(shares * price, 2)

        async with self.transaction() as db:
            if await self._shares(db, user_id, symbol) < shares:
                raise ValueError("столько акций у тебя нет")

            await db.execute(
                "UPDATE users SET cash = cash + ? WHERE id = ?", (total, user_id)
            )

            await self._add_transaction(db, user_id, symbol, shares, price, "sell")

    async def _add_transaction(self, db, user_id, symbol, shares, price, kind):
        await db.execute(
            """
            INSERT INTO transactions (user_id, symbol, shares, price, type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, symbol.upper(), shares, price, kind),
        )

    # HISTORY / PORTFOLIO

    async def history(self, user_id):
        return await self.fetchall(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )

    async def _shares(self, db, user_id: int, symbol: str) -> int:
        cursor = await db.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN type = 'buy' THEN shares ELSE -shares END), 0)
            FROM transactions WHERE user_id = ? AND symbol = ?
            """,
            (user_id, symbol.upper()),
        )

        return (await cursor.fetchone())[0]

    async def shares(self, user_id: int, symbol: str) -> int:
        async with self.connect() as db:
            return await self._shares(db, user_id, symbol)

    async def portfolio(self, user_id):
        return await self.fetchall(
            """
            SELECT symbol,
                   SUM(CASE WHEN type = 'buy' THEN shares ELSE -shares END) AS shares
            FROM transactions WHERE user_id = ?
            GROUP BY symbol HAVING shares > 0
            ORDER BY symbol
            """,
            (user_id,),
        )
