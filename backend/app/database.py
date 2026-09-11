import sqlite3
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("YUS_AI_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "yus_ai.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


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
