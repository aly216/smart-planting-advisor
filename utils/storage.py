"""SQLite 持久层：用户与种植日志。"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from utils.path_tool import get_abs_path
from utils.logger_handler import logger
from utils.config_handler import agent_conf

DB_PATH = get_abs_path(agent_conf.get('db_path', 'data/app.db'))

_SEED_PROFILES = [
    "阳台菜园 | 番茄 · 黄瓜",
    "露台 | 辣椒 · 茄子",
    "小院 | 月季 · 绣球",
    "阳台 | 多肉 · 绿萝",
    "屋顶花园 | 草莓 · 生菜",
    "院子 | 苹果 · 葡萄",
    "阳台 | 香草 · 薄荷",
    "露台 | 豆角 · 丝瓜",
    "小院 | 樱桃 · 蓝莓",
    "阳台 | 长寿花 · 栀子",
    "屋顶 | 西瓜 · 甜瓜",
    "院子 | 桃 · 柑橘",
]


def configure(db_path: str) -> None:
    global DB_PATH
    DB_PATH = db_path


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 多进程共享同一 DB 文件：WAL 允许读写并发，busy_timeout 等待锁释放
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _next_user_id(conn) -> str:
    row = conn.execute("SELECT MAX(CAST(user_id AS INTEGER)) AS m FROM users").fetchone()
    return str((row['m'] or 1000) + 1)


def _month_offset(months_back: int) -> str:
    d = datetime.now()
    total = d.year * 12 + (d.month - 1) - months_back
    y, m = divmod(total, 12)
    return f"{y}-{m + 1:02d}"


def init_db(db_path: str | None = None) -> None:
    if db_path is not None:
        configure(db_path)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT NOT NULL UNIQUE,
            username      TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            profile       TEXT,
            registered_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            month       TEXT NOT NULL,
            growth      TEXT,
            care        TEXT,
            comparison  TEXT,
            saved_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_user_month ON records(user_id, month);
        """)
    _seed_if_empty()


def _seed_if_empty() -> None:
    with _db() as conn:
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()['c'] > 0:
            return
    for i, profile in enumerate(_SEED_PROFILES, start=1):
        user_id = f"{1000 + i}"
        try:
            create_user(user_id, "123456", profile, user_id=user_id)
        except (ValueError, sqlite3.IntegrityError):
            # 并发播种：另一进程已抢先建好该种子用户，本次播种让路
            logger.warning(f"[seed] 种子用户 {user_id} 已存在，跳过本次播种")
            return
        for back in range(11, -1, -1):
            month = _month_offset(back)
            height = 20 + (i + back) % 40
            growth = f"株高{height}cm\n叶片{6 + (i + back) % 8}片 开花{(i + back) % 5}朵"
            care = f"本月浇水{8 + (i * 2 + back) % 15}次\n施肥:{'有机肥' if back % 2 else '复合肥'}{20 + (i + back) % 30}g"
            comparison = f"长势优于{(i + back) % 30 + 20}%同期"
            save_record(user_id, growth, care, comparison, month)


def create_user(username: str, password: str, profile: str, user_id: str | None = None) -> str:
    with _db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("用户名已存在")
        if user_id is None:
            user_id = _next_user_id(conn)
        conn.execute(
            "INSERT INTO users (user_id, username, password, profile, registered_at) VALUES (?,?,?,?,?)",
            (user_id, username, password, profile, datetime.now().isoformat()),
        )
    return user_id


def verify_user(username: str, password: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return None
    if row['password'] == password:
        return row['user_id']
    return None


def get_user(user_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return {
        "user_id": row['user_id'],
        "username": row['username'],
        "profile": row['profile'],
        "password": row['password'],
        "registered_at": row['registered_at'],
    }


def save_record(user_id: str, growth: str, care: str, comparison: str,
                month: str | None = None) -> None:
    if month is None:
        month = datetime.now().strftime('%Y-%m')
    with _db() as conn:
        conn.execute(
            "INSERT INTO records (user_id, month, growth, care, comparison, saved_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, month, growth, care, comparison, datetime.now().isoformat()),
        )


def fetch_record(user_id: str, month: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT r.growth, r.care, r.comparison, u.profile "
            "FROM records r JOIN users u ON u.user_id = r.user_id "
            "WHERE r.user_id = ? AND r.month = ? "
            "ORDER BY r.saved_at DESC, r.id DESC LIMIT 1",
            (user_id, month),
        ).fetchone()
    if row is None:
        return None
    return {"特征": row['profile'], "生长": row['growth'], "养护": row['care'], "对比": row['comparison']}


def format_record(record: dict | None) -> str:
    if record is None:
        return ""
    return "\n".join([f"特征:{record['特征']}", f"生长:{record['生长']}",
                      f"养护:{record['养护']}", f"对比:{record['对比']}"])
