from __future__ import annotations

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.path)
            self._db.row_factory = aiosqlite.Row
        return self._db

    async def init(self) -> None:
        db = await self._conn()
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                channel_id   INTEGER,
                status       TEXT    DEFAULT 'open',
                ticket_type  TEXT    DEFAULT 'family',
                name         TEXT,
                age          TEXT,
                about        TEXT,
                answers_json TEXT    DEFAULT '{}',
                deny_reason  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at    TIMESTAMP,
                closed_by    INTEGER,
                result       TEXT
            );

            CREATE TABLE IF NOT EXISTS fleet (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                plate       TEXT    DEFAULT '',
                taken_by    INTEGER,
                taken_at    TIMESTAMP,
                guild_id    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS afk (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                reason      TEXT,
                until       TIMESTAMP,
                message_id  INTEGER,
                active      INTEGER DEFAULT 1
            );
            """
        )
        # Migrate older schemas
        for col, sql in [
            ("ticket_type", "ALTER TABLE tickets ADD COLUMN ticket_type TEXT DEFAULT 'family'"),
            ("deny_reason", "ALTER TABLE tickets ADD COLUMN deny_reason TEXT"),
            ("answers_json", "ALTER TABLE tickets ADD COLUMN answers_json TEXT DEFAULT '{}'"),
            ("plate", "ALTER TABLE fleet ADD COLUMN plate TEXT DEFAULT ''"),
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()

    # ── Tickets ──────────────────────────────────────────────

    async def create_ticket(
        self, user_id: int, channel_id: int,
        ticket_type: str = "family",
        answers_json: str = "{}",
    ) -> int:
        db = await self._conn()
        cur = await db.execute(
            "INSERT INTO tickets (user_id, channel_id, ticket_type, answers_json) "
            "VALUES (?, ?, ?, ?)",
            (user_id, channel_id, ticket_type, answers_json),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def close_ticket(
        self, channel_id: int, closed_by: int, result: str, deny_reason: str | None = None,
    ) -> dict | None:
        db = await self._conn()
        cur = await db.execute(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'", (channel_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        await db.execute(
            "UPDATE tickets SET status='closed', closed_at=CURRENT_TIMESTAMP, "
            "closed_by=?, result=?, deny_reason=? WHERE id=?",
            (closed_by, result, deny_reason, row["id"]),
        )
        await db.commit()
        return dict(row)

    async def get_ticket_by_channel(self, channel_id: int) -> dict | None:
        db = await self._conn()
        cur = await db.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    # ── Fleet ────────────────────────────────────────────────

    async def add_car(self, guild_id: int, name: str, plate: str = "") -> int:
        db = await self._conn()
        cur = await db.execute(
            "INSERT INTO fleet (name, plate, guild_id) VALUES (?, ?, ?)",
            (name, plate, guild_id),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def remove_car(self, car_id: int) -> None:
        db = await self._conn()
        await db.execute("DELETE FROM fleet WHERE id = ?", (car_id,))
        await db.commit()

    async def get_cars(self, guild_id: int) -> list[dict]:
        db = await self._conn()
        cur = await db.execute(
            "SELECT * FROM fleet WHERE guild_id = ? ORDER BY id", (guild_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def take_car(self, car_id: int, user_id: int) -> bool:
        db = await self._conn()
        cur = await db.execute(
            "SELECT * FROM fleet WHERE id = ? AND taken_by IS NULL", (car_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return False
        await db.execute(
            "UPDATE fleet SET taken_by = ?, taken_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id, car_id),
        )
        await db.commit()
        return True

    async def release_car(self, car_id: int, user_id: int) -> bool:
        db = await self._conn()
        cur = await db.execute(
            "SELECT * FROM fleet WHERE id = ? AND taken_by = ?", (car_id, user_id)
        )
        row = await cur.fetchone()
        if row is None:
            return False
        await db.execute(
            "UPDATE fleet SET taken_by = NULL, taken_at = NULL WHERE id = ?", (car_id,)
        )
        await db.commit()
        return True

    async def release_all_by_user(self, user_id: int, guild_id: int) -> int:
        db = await self._conn()
        cur = await db.execute(
            "UPDATE fleet SET taken_by = NULL, taken_at = NULL "
            "WHERE taken_by = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        await db.commit()
        return cur.rowcount

    async def force_release_car(self, car_id: int) -> None:
        db = await self._conn()
        await db.execute(
            "UPDATE fleet SET taken_by = NULL, taken_at = NULL WHERE id = ?", (car_id,)
        )
        await db.commit()

    async def get_car(self, car_id: int) -> dict | None:
        db = await self._conn()
        cur = await db.execute("SELECT * FROM fleet WHERE id = ?", (car_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    # ── AFK ──────────────────────────────────────────────────

    async def add_afk(
        self, user_id: int, guild_id: int, reason: str, until: str, message_id: int,
    ) -> int:
        db = await self._conn()
        cur = await db.execute(
            "INSERT INTO afk (user_id, guild_id, reason, until, message_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, guild_id, reason, until, message_id),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_active_afks(self, guild_id: int) -> list[dict]:
        db = await self._conn()
        cur = await db.execute(
            "SELECT * FROM afk WHERE guild_id = ? AND active = 1 ORDER BY until",
            (guild_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def deactivate_afk(self, afk_id: int) -> None:
        db = await self._conn()
        await db.execute("UPDATE afk SET active = 0 WHERE id = ?", (afk_id,))
        await db.commit()

    async def deactivate_afk_by_user(self, user_id: int, guild_id: int) -> None:
        db = await self._conn()
        await db.execute(
            "UPDATE afk SET active = 0 WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        await db.commit()
