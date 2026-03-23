import json
import sqlite3
import threading
from pathlib import Path
from typing import Dict


class PluginSecretStore:
    def __init__(self, db_path: str = "data/plugin_secrets.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS plugin_secrets (
                        plugin_trigger TEXT NOT NULL,
                        secret_key TEXT NOT NULL,
                        secret_value TEXT NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (plugin_trigger, secret_key)
                    )
                    """
                )
                conn.commit()

    def get_secret(self, plugin_trigger: str, secret_key: str) -> str:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT secret_value FROM plugin_secrets WHERE plugin_trigger = ? AND secret_key = ?",
                    (plugin_trigger, secret_key),
                ).fetchone()
                return str(row["secret_value"]) if row else ""

    def get_all_for_plugin(self, plugin_trigger: str) -> Dict[str, str]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT secret_key, secret_value FROM plugin_secrets WHERE plugin_trigger = ?",
                    (plugin_trigger,),
                ).fetchall()
                return {
                    str(row["secret_key"]): str(row["secret_value"]) for row in rows
                }

    def set_secret(self, plugin_trigger: str, secret_key: str, secret_value: str):
        with self._lock:
            with self._connect() as conn:
                if secret_value:
                    conn.execute(
                        """
                        INSERT INTO plugin_secrets (plugin_trigger, secret_key, secret_value, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(plugin_trigger, secret_key)
                        DO UPDATE SET secret_value = excluded.secret_value, updated_at = CURRENT_TIMESTAMP
                        """,
                        (plugin_trigger, secret_key, secret_value),
                    )
                else:
                    conn.execute(
                        "DELETE FROM plugin_secrets WHERE plugin_trigger = ? AND secret_key = ?",
                        (plugin_trigger, secret_key),
                    )
                conn.commit()

    def export_debug_view(self) -> str:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT plugin_trigger, secret_key, updated_at FROM plugin_secrets ORDER BY plugin_trigger, secret_key"
                ).fetchall()
                return json.dumps(
                    [dict(row) for row in rows], ensure_ascii=False, indent=2
                )
