import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("YUS_AI_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "yus_ai.db"


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                base_url TEXT NOT NULL DEFAULT 'https://api.openai.com/v1',
                api_key TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                temperature REAL NOT NULL DEFAULT 0.8,
                max_tokens INTEGER NOT NULL DEFAULT 2048
            );
            INSERT OR IGNORE INTO settings (id) VALUES (1);

            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                system_prompt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT '新对话',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS pet_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                position_x REAL,
                position_y REAL
            );
            INSERT OR IGNORE INTO pet_state (id) VALUES (1);
            """
        )
        existing = {row[1] for row in db.execute("PRAGMA table_info(characters)")}
        for name in ("avatar_data", "greeting", "background", "personality", "speaking_style", "relationship", "boundaries", "example_dialogue"):
            if name not in existing:
                db.execute(f"ALTER TABLE characters ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        setting_columns = {row[1] for row in db.execute("PRAGMA table_info(settings)")}
        if "context_message_limit" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN context_message_limit INTEGER NOT NULL DEFAULT 20")
        if "memory_limit" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN memory_limit INTEGER NOT NULL DEFAULT 5")
        if "message_display_mode" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN message_display_mode TEXT NOT NULL DEFAULT 'markdown'")
        if "translation_mirror_url" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN translation_mirror_url TEXT NOT NULL DEFAULT ''")
        conversation_columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
        if "summary" not in conversation_columns:
            db.execute("ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS proactive_plugin (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_minutes INTEGER NOT NULL DEFAULT 30,
                max_tokens INTEGER NOT NULL DEFAULT 160,
                news_enabled INTEGER NOT NULL DEFAULT 0,
                rss_url TEXT NOT NULL DEFAULT 'https://www.chinanews.com.cn/rss/scroll-news.xml',
                next_due REAL NOT NULL DEFAULT 0,
                last_content TEXT NOT NULL DEFAULT '',
                total_tokens INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO proactive_plugin (id) VALUES (1);
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                source_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(character_id, content)
            );
        """)
        proactive_columns = {row[1] for row in db.execute("PRAGMA table_info(proactive_plugin)")}
        if "randomize_interval" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN randomize_interval INTEGER NOT NULL DEFAULT 1")
        if "failure_count" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE proactive_plugin SET max_tokens=1024 WHERE max_tokens=160")
