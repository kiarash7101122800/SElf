"""Encrypted-at-rest storage for Telegram StringSession secrets."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


SESSION_PREFIX = "selfbot:v1:"
KEY_FILENAME = ".session.key"


def _session_key(data_dir: str | Path) -> bytes:
    configured = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()
    if configured:
        key = configured.encode("ascii")
        Fernet(key)
        return key

    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    key_path = root / KEY_FILENAME
    try:
        descriptor = os.open(
            key_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    Fernet(key)
    return key


def encrypt_session(data_dir: str | Path, session_string: str) -> str:
    plaintext = str(session_string or "").strip()
    if not plaintext:
        raise ValueError("سشن تلگرام خالی است.")
    if plaintext.startswith(SESSION_PREFIX):
        # Validate encrypted input instead of nesting encryption.
        decrypt_session(data_dir, plaintext)
        return plaintext
    token = Fernet(_session_key(data_dir)).encrypt(
        plaintext.encode("utf-8")
    )
    return SESSION_PREFIX + token.decode("ascii")


def decrypt_session(data_dir: str | Path, stored_value: str) -> str:
    stored = str(stored_value or "").strip()
    if not stored:
        raise ValueError("سشن تلگرام خالی است.")
    if not stored.startswith(SESSION_PREFIX):
        return stored
    token = stored.removeprefix(SESSION_PREFIX).encode("ascii")
    try:
        plaintext = Fernet(_session_key(data_dir)).decrypt(token)
    except (InvalidToken, ValueError) as exc:
        raise ValueError(
            "سشن رمزنگاری‌شده معتبر نیست یا کلید آن تغییر کرده است."
        ) from exc
    return plaintext.decode("utf-8")


def write_session_file(
    path: str | Path,
    data_dir: str | Path,
    session_string: str,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt_session(data_dir, session_string)
    temporary = target.with_suffix(target.suffix + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def read_session_file(
    path: str | Path,
    data_dir: str | Path,
    *,
    migrate_plaintext: bool = True,
) -> str:
    target = Path(path)
    stored = target.read_text(encoding="utf-8").strip()
    plaintext = decrypt_session(data_dir, stored)
    if migrate_plaintext and not stored.startswith(SESSION_PREFIX):
        write_session_file(target, data_dir, plaintext)
    return plaintext
