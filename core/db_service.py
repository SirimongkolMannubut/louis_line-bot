import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


class ConnectionWrapper:
    def __init__(self, conn, db_type):
        self._conn = conn
        self.db_type = db_type

    def execute(self, query, params=None):
        cursor = self._conn.cursor()
        if self.db_type == "postgres":
            # Convert SQLite parameter placeholder '?' to PostgreSQL '%s'
            query = query.replace("?", "%s")
            cursor.execute(query, params)
        else:
            cursor.execute(query, params or ())
        return cursor

    def executescript(self, script_str):
        if self.db_type == "postgres":
            cursor = self._conn.cursor()
            cursor.execute(script_str)
            return cursor
        else:
            return self._conn.executescript(script_str)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


@contextmanager
def get_conn():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        wrapper = ConnectionWrapper(conn, "postgres")
        try:
            yield wrapper
            wrapper.commit()
        except Exception:
            wrapper.rollback()
            raise
        finally:
            wrapper.close()
    else:
        DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "louisai.db"
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        wrapper = ConnectionWrapper(conn, "sqlite")
        try:
            yield wrapper
            wrapper.commit()
        except Exception:
            wrapper.rollback()
            raise
        finally:
            wrapper.close()


def init_db():
    with get_conn() as conn:
        if conn.db_type == "postgres":
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id        SERIAL PRIMARY KEY,
                user_id   TEXT    NOT NULL,
                type      TEXT    NOT NULL,
                amount    DOUBLE PRECISION NOT NULL,
                category  TEXT,
                note      TEXT,
                date      TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id          SERIAL PRIMARY KEY,
                user_id     TEXT NOT NULL,
                title       TEXT NOT NULL,
                event_date  TEXT NOT NULL,
                event_time  TEXT,
                notified    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS slips (
                id        SERIAL PRIMARY KEY,
                user_id   TEXT NOT NULL,
                amount    DOUBLE PRECISION,
                bank      TEXT,
                ref       TEXT,
                datetime  TEXT,
                raw_text  TEXT,
                created   TEXT NOT NULL,
                batch_id  TEXT
            );
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id    TEXT PRIMARY KEY,
                name       TEXT,
                age        TEXT,
                job        TEXT,
                location   TEXT,
                data_json  TEXT DEFAULT '{}',
                updated_at TEXT
            );
            """)
        else:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT    NOT NULL,
                type      TEXT    NOT NULL,
                amount    REAL    NOT NULL,
                category  TEXT,
                note      TEXT,
                date      TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                title       TEXT NOT NULL,
                event_date  TEXT NOT NULL,
                event_time  TEXT,
                notified    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS slips (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT NOT NULL,
                amount    REAL,
                bank      TEXT,
                ref       TEXT,
                datetime  TEXT,
                raw_text  TEXT,
                created   TEXT NOT NULL,
                batch_id  TEXT
            );
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id    TEXT PRIMARY KEY,
                name       TEXT,
                age        TEXT,
                job        TEXT,
                location   TEXT,
                data_json  TEXT DEFAULT '{}',
                updated_at TEXT
            );
            """)
            try:
                conn.execute("ALTER TABLE slips ADD COLUMN batch_id TEXT")
            except Exception:
                pass


# ── Transactions ──────────────────────────────────────────────────────────────
def add_transaction(
    user_id: str, type_: str, amount: float, category: str = "", note: str = ""
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO transactions (user_id,type,amount,category,note,date) VALUES (?,?,?,?,?,?)",
            (
                user_id,
                type_,
                amount,
                category,
                note,
                datetime.now().strftime("%Y-%m-%d"),
            ),
        )


def get_monthly_summary(user_id: str, year: int, month: int) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT type, SUM(amount) as total FROM transactions "
            "WHERE user_id=? AND substr(date, 1, 7)=? GROUP BY type",
            (user_id, f"{year:04d}-{month:02d}"),
        ).fetchall()
    income = expense = 0.0
    for r in rows:
        if r["type"] == "income":
            income = r["total"]
        else:
            expense = r["total"]
    return {"income": income, "expense": expense, "balance": income - expense}


def get_recent_transactions(user_id: str, limit: int = 10) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


# ── Events ────────────────────────────────────────────────────────────────────
def add_event(user_id: str, title: str, event_date: str, event_time: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (user_id,title,event_date,event_time) VALUES (?,?,?,?)",
            (user_id, title, event_date, event_time),
        )


def get_upcoming_events(user_id: str, limit: int = 10) -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE user_id=? AND event_date>=? ORDER BY event_date,event_time LIMIT ?",
            (user_id, today, limit),
        ).fetchall()


def get_pending_notifications() -> list:
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M")
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE notified=0 AND event_date=? AND event_time<=?",
            (date, time),
        ).fetchall()


def mark_notified(event_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE events SET notified=1 WHERE id=?", (event_id,))


# ── Slips ─────────────────────────────────────────────────────────────────────
def save_slip(
    user_id: str,
    amount: float | None,
    bank: str,
    ref: str,
    dt: str,
    raw_text: str,
    batch_id: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO slips (user_id,amount,bank,ref,datetime,raw_text,created,batch_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                user_id,
                amount,
                bank,
                ref,
                dt,
                raw_text,
                datetime.now().isoformat(),
                batch_id,
            ),
        )


def get_latest_slip_batch(user_id: str) -> list[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT batch_id FROM slips WHERE user_id=? AND batch_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            return []
        batch_id = row["batch_id"]
        rows = conn.execute(
            "SELECT * FROM slips WHERE user_id=? AND batch_id=? ORDER BY id ASC",
            (user_id, batch_id),
        ).fetchall()
        return [dict(r) for r in rows]


init_db()
