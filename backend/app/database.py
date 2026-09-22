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
                max_tokens INTEGER NOT NULL DEFAULT 2048,
                include_local_time INTEGER NOT NULL DEFAULT 1,
                include_location_context INTEGER NOT NULL DEFAULT 0,
                location_context TEXT NOT NULL DEFAULT ''
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
                origin TEXT NOT NULL DEFAULT 'chat',
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
        if "vision_model" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN vision_model TEXT NOT NULL DEFAULT ''")
        if "document_analysis_mode" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN document_analysis_mode TEXT NOT NULL DEFAULT 'fast'")
        if "include_local_time" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN include_local_time INTEGER NOT NULL DEFAULT 1")
        if "include_location_context" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN include_location_context INTEGER NOT NULL DEFAULT 0")
        if "location_context" not in setting_columns:
            db.execute("ALTER TABLE settings ADD COLUMN location_context TEXT NOT NULL DEFAULT ''")
        conversation_columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
        if "summary" not in conversation_columns:
            db.execute("ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        if "origin" not in message_columns:
            db.execute("ALTER TABLE messages ADD COLUMN origin TEXT NOT NULL DEFAULT 'chat'")
            db.execute("UPDATE conversations SET summary='' ")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS proactive_plugin (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_minutes INTEGER NOT NULL DEFAULT 30,
                random_min_minutes INTEGER NOT NULL DEFAULT 15,
                random_max_minutes INTEGER NOT NULL DEFAULT 60,
                history_weight INTEGER NOT NULL DEFAULT 15,
                care_enabled INTEGER NOT NULL DEFAULT 1,
                care_weight INTEGER NOT NULL DEFAULT 45,
                last_care_slot TEXT NOT NULL DEFAULT '',
                max_tokens INTEGER NOT NULL DEFAULT 160,
                news_enabled INTEGER NOT NULL DEFAULT 0,
                rss_url TEXT NOT NULL DEFAULT 'https://www.chinanews.com.cn/rss/scroll-news.xml',
                next_due REAL NOT NULL DEFAULT 0,
                last_content TEXT NOT NULL DEFAULT '',
                total_tokens INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                char_count INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                analysis_mode TEXT NOT NULL DEFAULT 'fast',
                analysis_stage TEXT NOT NULL DEFAULT '',
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                page_number INTEGER,
                content TEXT NOT NULL
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
        document_columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        if "image_count" not in document_columns:
            db.execute("ALTER TABLE documents ADD COLUMN image_count INTEGER NOT NULL DEFAULT 0")
        if "analysis_mode" not in document_columns:
            db.execute("ALTER TABLE documents ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'fast'")
        if "analysis_stage" not in document_columns:
            db.execute("ALTER TABLE documents ADD COLUMN analysis_stage TEXT NOT NULL DEFAULT ''")
        if "progress_current" not in document_columns:
            db.execute("ALTER TABLE documents ADD COLUMN progress_current INTEGER NOT NULL DEFAULT 0")
        if "progress_total" not in document_columns:
            db.execute("ALTER TABLE documents ADD COLUMN progress_total INTEGER NOT NULL DEFAULT 0")
        proactive_columns = {row[1] for row in db.execute("PRAGMA table_info(proactive_plugin)")}
        if "randomize_interval" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN randomize_interval INTEGER NOT NULL DEFAULT 1")
        if "random_min_minutes" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN random_min_minutes INTEGER NOT NULL DEFAULT 15")
        if "random_max_minutes" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN random_max_minutes INTEGER NOT NULL DEFAULT 60")
        if "history_weight" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN history_weight INTEGER NOT NULL DEFAULT 15")
        if "care_enabled" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN care_enabled INTEGER NOT NULL DEFAULT 1")
        if "care_weight" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN care_weight INTEGER NOT NULL DEFAULT 45")
        if "last_care_slot" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN last_care_slot TEXT NOT NULL DEFAULT ''")
        if "failure_count" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
        if "last_error" not in proactive_columns:
            db.execute("ALTER TABLE proactive_plugin ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
        if "failure_count" not in proactive_columns:
            db.execute("UPDATE proactive_plugin SET max_tokens=1024 WHERE max_tokens=160")
