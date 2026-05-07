"""SQLite 连接管理 — 单文件数据库，无需连接池."""
import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

DB_DIR = Path(__file__).parent
DEFAULT_DB_PATH = DB_DIR / "poi.db"


def get_db_path() -> str:
    """从环境变量读取 DB 路径，默认 db/poi.db."""
    return os.getenv("POI_DB_PATH", str(DEFAULT_DB_PATH))


@contextmanager
def get_conn(db_path: str = None):
    """获取 SQLite 连接（上下文管理器，自动提交/关闭）."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = None):
    """初始化数据库，执行 schema.sql."""
    path = db_path or get_db_path()
    schema_file = DB_DIR / "schema.sql"
    with get_conn(path) as conn:
        schema = schema_file.read_text(encoding="utf-8")
        conn.executescript(schema)
