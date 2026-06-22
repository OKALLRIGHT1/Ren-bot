import base64
import ctypes
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Dict


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


class PluginSecretStore:
    _DPAPI_PREFIX = "dpapi:v1:"
    _PLAIN_PREFIX = "plain:v1:"

    def __init__(self, db_path: str = "data/plugin_secrets.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_plaintext_secrets()

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

    @classmethod
    def _dpapi_available(cls) -> bool:
        return os.name == "nt" and hasattr(ctypes, "windll")

    @classmethod
    def _dpapi_protect(cls, value: bytes) -> bytes:
        if not cls._dpapi_available():
            raise RuntimeError("Windows DPAPI is not available")
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        input_buffer = ctypes.create_string_buffer(value)
        input_blob = _DataBlob(
            len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_char))
        )
        output_blob = _DataBlob()
        if not crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    @classmethod
    def _dpapi_unprotect(cls, value: bytes) -> bytes:
        if not cls._dpapi_available():
            raise RuntimeError("Windows DPAPI is not available")
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        input_buffer = ctypes.create_string_buffer(value)
        input_blob = _DataBlob(
            len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_char))
        )
        output_blob = _DataBlob()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    @classmethod
    def _encode_secret_value(cls, secret_value: str) -> str:
        value = str(secret_value or "")
        if not value:
            return ""
        if not cls._dpapi_available():
            return cls._PLAIN_PREFIX + value
        encrypted = cls._dpapi_protect(value.encode("utf-8"))
        return cls._DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")

    @classmethod
    def _decode_secret_value(cls, stored_value: str) -> str:
        value = str(stored_value or "")
        if not value:
            return ""
        if value.startswith(cls._DPAPI_PREFIX):
            encoded = value[len(cls._DPAPI_PREFIX) :]
            encrypted = base64.b64decode(encoded.encode("ascii"))
            return cls._dpapi_unprotect(encrypted).decode("utf-8")
        if value.startswith(cls._PLAIN_PREFIX):
            return value[len(cls._PLAIN_PREFIX) :]
        return value

    def _migrate_plaintext_secrets(self) -> None:
        if not self._dpapi_available():
            return
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT plugin_trigger, secret_key, secret_value FROM plugin_secrets"
                ).fetchall()
                for row in rows:
                    stored_value = str(row["secret_value"] or "")
                    if (
                        not stored_value
                        or stored_value.startswith(self._DPAPI_PREFIX)
                        or stored_value.startswith(self._PLAIN_PREFIX)
                    ):
                        continue
                    encrypted_value = self._encode_secret_value(stored_value)
                    conn.execute(
                        """
                        UPDATE plugin_secrets
                        SET secret_value = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE plugin_trigger = ? AND secret_key = ?
                        """,
                        (
                            encrypted_value,
                            str(row["plugin_trigger"]),
                            str(row["secret_key"]),
                        ),
                    )
                conn.commit()

    def get_secret(self, plugin_trigger: str, secret_key: str) -> str:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT secret_value FROM plugin_secrets WHERE plugin_trigger = ? AND secret_key = ?",
                    (plugin_trigger, secret_key),
                ).fetchone()
                return self._decode_secret_value(str(row["secret_value"])) if row else ""

    def get_all_for_plugin(self, plugin_trigger: str) -> Dict[str, str]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT secret_key, secret_value FROM plugin_secrets WHERE plugin_trigger = ?",
                    (plugin_trigger,),
                ).fetchall()
                return {
                    str(row["secret_key"]): self._decode_secret_value(
                        str(row["secret_value"])
                    )
                    for row in rows
                }

    def set_secret(self, plugin_trigger: str, secret_key: str, secret_value: str):
        with self._lock:
            with self._connect() as conn:
                if secret_value:
                    stored_value = self._encode_secret_value(secret_value)
                    conn.execute(
                        """
                        INSERT INTO plugin_secrets (plugin_trigger, secret_key, secret_value, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(plugin_trigger, secret_key)
                        DO UPDATE SET secret_value = excluded.secret_value, updated_at = CURRENT_TIMESTAMP
                        """,
                        (plugin_trigger, secret_key, stored_value),
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
