"""Persistent services for the self-builder administration control center.

The module intentionally contains no Telegram handlers.  It owns schema
migrations and small, testable operations while ``admin_ui.py`` provides the
inline-panel experience.
"""

from __future__ import annotations

import json
import io
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ADMIN_ROLES = {
    "owner": {"*"},
    "manager": {
        "dashboard",
        "users",
        "selfs",
        "subscriptions",
        "finance",
        "features",
        "broadcast",
        "support",
        "settings",
        "reports",
    },
    "finance": {"dashboard", "users", "subscriptions", "finance", "reports"},
    "support": {"dashboard", "users", "selfs", "support"},
    "operator": {
        "dashboard",
        "users",
        "selfs",
        "broadcast",
        "support",
        "reports",
    },
    "viewer": {"dashboard", "reports"},
}


FEATURE_CATALOG = {
    "private_lock": ("قفل کامل پیوی", "private_lock_enabled"),
    "anti_delete": ("ضدحذف", "anti_delete_enabled"),
    "anti_edit_private": ("ضد ویرایش پیوی", "anti_edit_private"),
    "anti_edit_groups": ("ضد ویرایش گروه", "anti_edit_groups"),
    "secretary": ("منشی و پاسخ خودکار", "secretary_enabled"),
    "forms": ("فرم‌ساز سفارش", "forms_enabled"),
    "group_management": ("مدیریت گروه", "group_management_enabled"),
    "scheduling": ("زمان‌بندی و ارسال خودکار", "scheduled_messages_enabled"),
    "profile_tools": ("ابزار پروفایل و ساعت", "profile_tools_enabled"),
    "games": ("سرگرمی و بازی", "games_enabled"),
    "media_tools": ("دانلود و ابزار رسانه", "media_tools_enabled"),
    "chatgpt": ("ابزارهای ChatGPT", "chatgpt_enabled"),
}

CURRENT_RELEASE = "2.12.3"


class ClosingConnection(sqlite3.Connection):
    """SQLite connection that truly closes when leaving a ``with`` block."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


class AdminCenterStore:
    def __init__(
        self,
        users_db: str | Path,
        data_dir: str | Path,
        sessions_dir: str | Path,
    ) -> None:
        self.users_db = Path(users_db)
        self.data_dir = Path(data_dir)
        self.sessions_dir = Path(sessions_dir)
        self.backup_dir = self.data_dir / "admin_backups"
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.users_db, timeout=20, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_schema(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_roles (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                    ON admin_audit_log(created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    duration_days INTEGER NOT NULL CHECK(duration_days >= 0),
                    price_coins INTEGER NOT NULL CHECK(price_coins >= 0),
                    trial_days INTEGER NOT NULL DEFAULT 0,
                    feature_keys TEXT NOT NULL DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    plan_id INTEGER NOT NULL,
                    starts_at TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    auto_renew INTEGER NOT NULL DEFAULT 0,
                    assigned_by INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES subscription_plans(id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user
                    ON user_subscriptions(user_id, status, id DESC);

                CREATE TABLE IF NOT EXISTS discount_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
                    max_uses INTEGER NOT NULL DEFAULT 0,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feature_policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_type TEXT NOT NULL
                        CHECK(scope_type IN ('global', 'plan', 'user')),
                    scope_id TEXT NOT NULL DEFAULT '',
                    feature_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope_type, scope_id, feature_key)
                );

                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    segment TEXT NOT NULL,
                    body TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_broadcast_due
                    ON broadcasts(status, scheduled_at, id);
                CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                    broadcast_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    delivered_at TEXT,
                    PRIMARY KEY(broadcast_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS support_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subject TEXT NOT NULL DEFAULT 'پشتیبانی',
                    status TEXT NOT NULL DEFAULT 'open',
                    assigned_admin_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_support_ticket_status
                    ON support_tickets(status, updated_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS support_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    sender_type TEXT NOT NULL,
                    sender_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES support_tickets(id)
                );

                CREATE TABLE IF NOT EXISTS force_join_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL UNIQUE,
                    username TEXT,
                    title TEXT NOT NULL,
                    join_url TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    restored_at TEXT
                );
                CREATE TABLE IF NOT EXISTS runtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    user_id INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_events_created
                    ON runtime_events(created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS login_security (
                    user_id INTEGER PRIMARY KEY,
                    window_started_at TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    failed_code_count INTEGER NOT NULL DEFAULT 0,
                    blocked_until TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscription_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    threshold_days INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(user_id, expires_at, threshold_days)
                );
                CREATE INDEX IF NOT EXISTS idx_subscription_notice_pending
                    ON subscription_notifications(status, id);

                CREATE TABLE IF NOT EXISTS admin_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    user_id INTEGER,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_admin_notice_pending
                    ON admin_notifications(status, id);

                CREATE TABLE IF NOT EXISTS self_release_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    from_version TEXT,
                    to_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    admin_id INTEGER,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_release_user
                    ON self_release_history(user_id, id DESC);
                """
            )
            user_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            user_migrations = {
                "self_version": "TEXT",
                "self_previous_version": "TEXT",
                "self_code_hash": "TEXT",
                "self_last_updated_at": "TEXT",
            }
            for column, column_type in user_migrations.items():
                if column not in user_columns:
                    conn.execute(
                        f"ALTER TABLE users ADD COLUMN {column} {column_type}"
                    )
            # A process can stop while a broadcast is running.  Re-queue it;
            # already successful recipients are excluded when it resumes.
            conn.execute(
                """UPDATE broadcasts SET status = 'pending', started_at = NULL
                   WHERE status = 'running' AND cancel_requested = 0"""
            )
            existing = conn.execute(
                "SELECT COUNT(*) FROM subscription_plans"
            ).fetchone()[0]
            if not existing:
                now = utcnow()
                feature_json = json.dumps(
                    list(FEATURE_CATALOG), ensure_ascii=False
                )
                conn.executemany(
                    """INSERT INTO subscription_plans
                       (name, duration_days, price_coins, trial_days,
                        feature_keys, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    [
                        ("آزمایشی", 3, 0, 3, feature_json, now, now),
                        ("ماهانه", 30, 0, 0, feature_json, now, now),
                        ("دائمی", 0, 0, 0, feature_json, now, now),
                    ],
                )

    def audit(
        self,
        admin_id: int | None,
        action: str,
        target_type: str = "",
        target_id: str | int = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO admin_audit_log
                   (admin_id, action, target_type, target_id, detail_json,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    admin_id,
                    action,
                    target_type,
                    str(target_id),
                    json.dumps(detail or {}, ensure_ascii=False),
                    utcnow(),
                ),
            )

    def event(
        self,
        level: str,
        component: str,
        message: str,
        user_id: int | None = None,
    ) -> None:
        safe_message = str(message).replace("\x00", "")[:1000]
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO runtime_events
                   (level, component, message, user_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (level[:20], component[:80], safe_message, user_id, utcnow()),
            )

    def register_login_request(
        self,
        user_id: int,
        *,
        max_requests: int = 3,
        window_minutes: int = 15,
        block_minutes: int = 30,
    ) -> tuple[bool, int]:
        """Atomically rate-limit Telegram login-code requests.

        The second return value is the number of seconds the user should wait.
        No phone number, code, password, or session secret is stored here.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM login_security WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            if row:
                blocked_until = parse_datetime(row["blocked_until"])
                if blocked_until and blocked_until > now:
                    return False, max(
                        1, int((blocked_until - now).total_seconds())
                    )
                window_started = (
                    parse_datetime(row["window_started_at"]) or now
                )
                request_count = int(row["request_count"] or 0)
                if now - window_started >= timedelta(minutes=window_minutes):
                    window_started = now
                    request_count = 0
                if request_count >= int(max_requests):
                    blocked_until = now + timedelta(minutes=block_minutes)
                    conn.execute(
                        """UPDATE login_security
                           SET blocked_until = ?, updated_at = ?
                           WHERE user_id = ?""",
                        (
                            blocked_until.isoformat(),
                            now.isoformat(),
                            int(user_id),
                        ),
                    )
                    return False, int(
                        (blocked_until - now).total_seconds()
                    )
                conn.execute(
                    """UPDATE login_security
                       SET window_started_at = ?, request_count = ?,
                           blocked_until = NULL, updated_at = ?
                       WHERE user_id = ?""",
                    (
                        window_started.isoformat(),
                        request_count + 1,
                        now.isoformat(),
                        int(user_id),
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO login_security
                       (user_id, window_started_at, request_count,
                        failed_code_count, blocked_until, updated_at)
                       VALUES (?, ?, 1, 0, NULL, ?)""",
                    (int(user_id), now.isoformat(), now.isoformat()),
                )
        return True, 0

    def record_login_failure(
        self,
        user_id: int,
        *,
        max_failures: int = 5,
        block_minutes: int = 30,
    ) -> tuple[bool, int]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT failed_code_count FROM login_security WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            failures = int(row["failed_code_count"] or 0) + 1 if row else 1
            blocked_until = (
                now + timedelta(minutes=block_minutes)
                if failures >= int(max_failures)
                else None
            )
            conn.execute(
                """INSERT INTO login_security
                   (user_id, window_started_at, request_count,
                    failed_code_count, blocked_until, updated_at)
                   VALUES (?, ?, 0, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     failed_code_count = excluded.failed_code_count,
                     blocked_until = excluded.blocked_until,
                     updated_at = excluded.updated_at""",
                (
                    int(user_id),
                    now.isoformat(),
                    failures,
                    blocked_until.isoformat() if blocked_until else None,
                    now.isoformat(),
                ),
            )
        return blocked_until is None, failures

    def reset_login_security(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE login_security
                   SET failed_code_count = 0, blocked_until = NULL,
                       updated_at = ?
                   WHERE user_id = ?""",
                (utcnow(), int(user_id)),
            )

    def login_security_summary(self) -> dict[str, int]:
        now = utcnow()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS tracked,
                          SUM(CASE WHEN blocked_until > ? THEN 1 ELSE 0 END)
                              AS blocked,
                          SUM(failed_code_count) AS failed
                   FROM login_security""",
                (now,),
            ).fetchone()
        return {
            "tracked": int(row["tracked"] or 0),
            "blocked": int(row["blocked"] or 0),
            "failed": int(row["failed"] or 0),
        }

    def queue_admin_notification(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        user_id: int | None = None,
        fingerprint: str,
    ) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO admin_notifications
                   (kind, title, body, user_id, fingerprint, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    str(kind)[:40],
                    str(title)[:200],
                    str(body)[:3000],
                    int(user_id) if user_id is not None else None,
                    str(fingerprint)[:300],
                    utcnow(),
                ),
            )
        return cursor.rowcount > 0

    def pending_admin_notifications(
        self, limit: int = 20
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM admin_notifications
                   WHERE status = 'pending' ORDER BY id LIMIT ?""",
                (max(1, min(int(limit), 100)),),
            ).fetchall()

    def mark_admin_notification_sent(self, notification_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE admin_notifications
                   SET status = 'sent', sent_at = ?
                   WHERE id = ?""",
                (utcnow(), int(notification_id)),
            )

    def process_auto_renewals(self) -> list[dict[str, Any]]:
        """Renew due subscriptions using wallet coins when enabled."""
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
        results: list[dict[str, Any]] = []
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT us.id, us.user_id, us.expires_at, sp.id AS plan_id,
                          sp.name AS plan_name, sp.duration_days,
                          sp.price_coins, u.coins
                   FROM user_subscriptions us
                   JOIN subscription_plans sp ON sp.id = us.plan_id
                   JOIN users u ON u.user_id = us.user_id
                   WHERE us.status = 'active' AND us.auto_renew = 1
                     AND us.expires_at IS NOT NULL
                     AND us.expires_at <= ?""",
                (now.isoformat(),),
            ).fetchall()
            for row in rows:
                price = int(row["price_coins"] or 0)
                balance = int(row["coins"] or 0)
                user_id = int(row["user_id"])
                days = int(row["duration_days"] or 0)
                if days <= 0:
                    conn.execute(
                        "UPDATE user_subscriptions SET auto_renew = 0 WHERE id = ?",
                        (int(row["id"]),),
                    )
                    continue
                if balance < price:
                    conn.execute(
                        """UPDATE user_subscriptions SET auto_renew = 0
                           WHERE id = ?""",
                        (int(row["id"]),),
                    )
                    results.append(
                        {
                            "user_id": user_id,
                            "renewed": False,
                            "reason": "insufficient_balance",
                            "price": price,
                            "balance": balance,
                        }
                    )
                    continue
                new_balance = balance - price
                new_expiry = now + timedelta(days=days)
                conn.execute(
                    """UPDATE user_subscriptions SET expires_at = ?
                       WHERE id = ?""",
                    (new_expiry.isoformat(), int(row["id"])),
                )
                conn.execute(
                    """UPDATE users SET coins = ?, expiration_date = ?,
                       is_active = 1, updated_at = ? WHERE user_id = ?""",
                    (
                        new_balance,
                        new_expiry.isoformat(),
                        now.isoformat(),
                        user_id,
                    ),
                )
                if price:
                    conn.execute(
                        """INSERT INTO balance_transactions
                           (user_id, amount, balance_after, transaction_type,
                            note)
                           VALUES (?, ?, ?, 'subscription_auto_renew', ?)""",
                        (
                            user_id,
                            -price,
                            new_balance,
                            f"تمدید خودکار پلن {row['plan_name']}",
                        ),
                    )
                results.append(
                    {
                        "user_id": user_id,
                        "renewed": True,
                        "plan_name": str(row["plan_name"]),
                        "price": price,
                        "balance": new_balance,
                        "expires_at": new_expiry.isoformat(),
                    }
                )
        for item in results:
            if item["renewed"]:
                self.audit(
                    None,
                    "subscription.auto_renew",
                    "user",
                    item["user_id"],
                    item,
                )
        return results

    def queue_due_expiry_notifications(self) -> list[sqlite3.Row]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT us.user_id, us.expires_at, us.auto_renew,
                          sp.name AS plan_name, sp.price_coins
                   FROM user_subscriptions us
                   JOIN subscription_plans sp ON sp.id = us.plan_id
                   WHERE us.status = 'active' AND us.expires_at IS NOT NULL
                   ORDER BY us.expires_at"""
            ).fetchall()
            for row in rows:
                expires = parse_datetime(row["expires_at"])
                if expires is None:
                    continue
                seconds = (expires - now).total_seconds()
                if seconds <= 0:
                    threshold = 0
                elif seconds <= 86400:
                    threshold = 1
                elif seconds <= 3 * 86400:
                    threshold = 3
                elif seconds <= 7 * 86400:
                    threshold = 7
                else:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO subscription_notifications
                       (user_id, expires_at, threshold_days, status, created_at)
                       VALUES (?, ?, ?, 'pending', ?)""",
                    (
                        int(row["user_id"]),
                        str(row["expires_at"]),
                        threshold,
                        utcnow(),
                    ),
                )
            return conn.execute(
                """SELECT sn.*, us.auto_renew, sp.name AS plan_name,
                          sp.price_coins
                   FROM subscription_notifications sn
                   LEFT JOIN user_subscriptions us
                     ON us.user_id = sn.user_id
                    AND us.expires_at = sn.expires_at
                    AND us.status = 'active'
                   LEFT JOIN subscription_plans sp ON sp.id = us.plan_id
                   WHERE sn.status = 'pending' AND sn.attempt_count < 5
                   ORDER BY sn.id LIMIT 50"""
            ).fetchall()

    def mark_expiry_notification(
        self, notification_id: int, *, sent: bool
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE subscription_notifications
                   SET status = ?, attempt_count = attempt_count + 1,
                       sent_at = CASE WHEN ? THEN ? ELSE sent_at END
                   WHERE id = ?""",
                (
                    "sent" if sent else "pending",
                    1 if sent else 0,
                    utcnow(),
                    int(notification_id),
                ),
            )

    def record_self_release(
        self,
        user_id: int,
        *,
        to_version: str,
        code_hash: str,
        status: str,
        reason: str,
        detail: str = "",
        admin_id: int | None = None,
    ) -> None:
        now = utcnow()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT self_version FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            if not row:
                return
            previous = str(row["self_version"] or "")
            conn.execute(
                """INSERT INTO self_release_history
                   (user_id, from_version, to_version, status, reason,
                    admin_id, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(user_id),
                    previous or None,
                    str(to_version),
                    str(status),
                    str(reason)[:100],
                    int(admin_id) if admin_id is not None else None,
                    str(detail)[:1000],
                    now,
                ),
            )
            if status == "success":
                conn.execute(
                    """UPDATE users SET self_previous_version = self_version,
                       self_version = ?, self_code_hash = ?,
                       self_last_updated_at = ?, updated_at = ?
                       WHERE user_id = ?""",
                    (
                        str(to_version),
                        str(code_hash),
                        now,
                        now,
                        int(user_id),
                    ),
                )

    def release_summary(self, current_version: str) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN self_version = ? THEN 1 ELSE 0 END)
                              AS current_count,
                          SUM(CASE WHEN COALESCE(self_version, '') != ?
                                   THEN 1 ELSE 0 END) AS outdated
                   FROM users
                   WHERE phone IS NOT NULL AND TRIM(phone) != ''
                     AND session_file IS NOT NULL
                     AND TRIM(session_file) != ''""",
                (str(current_version), str(current_version)),
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "current": int(row["current_count"] or 0),
            "outdated": int(row["outdated"] or 0),
        }

    def financial_summary(self, days: int = 30) -> dict[str, int]:
        since = (
            datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
            - timedelta(days=max(1, int(days)))
        ).isoformat()
        with self.connect() as conn:
            receipts = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END)
                              AS approved,
                          SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END)
                              AS pending,
                          COALESCE(SUM(CASE WHEN status = 'approved'
                                           THEN amount_toman ELSE 0 END), 0)
                              AS revenue
                   FROM payment_receipts WHERE created_at >= ?""",
                (since,),
            ).fetchone()
            wallet = conn.execute(
                """SELECT COUNT(*) AS movements,
                          COALESCE(SUM(CASE WHEN amount > 0
                                           THEN amount ELSE 0 END), 0)
                              AS credits,
                          COALESCE(SUM(CASE WHEN amount < 0
                                           THEN -amount ELSE 0 END), 0)
                              AS debits,
                          COALESCE(SUM(CASE
                              WHEN transaction_type = 'daily_self_fee'
                              THEN -amount ELSE 0 END), 0) AS daily_fees,
                          COALESCE(SUM(CASE
                              WHEN transaction_type = 'subscription_auto_renew'
                              THEN -amount ELSE 0 END), 0) AS renewals
                   FROM balance_transactions WHERE created_at >= ?""",
                (since,),
            ).fetchone()
        return {
            "receipts": int(receipts["total"] or 0),
            "approved": int(receipts["approved"] or 0),
            "pending": int(receipts["pending"] or 0),
            "revenue": int(receipts["revenue"] or 0),
            "movements": int(wallet["movements"] or 0),
            "credits": int(wallet["credits"] or 0),
            "debits": int(wallet["debits"] or 0),
            "daily_fees": int(wallet["daily_fees"] or 0),
            "renewals": int(wallet["renewals"] or 0),
        }

    def set_admin_role(
        self, user_id: int, role: str, updated_by: int
    ) -> None:
        if role not in ADMIN_ROLES or role == "owner":
            raise ValueError("نقش انتخاب‌شده معتبر نیست.")
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM app_admins WHERE user_id = ?", (int(user_id),)
            ).fetchone()
            if not exists:
                raise LookupError("ابتدا کاربر را به فهرست ادمین‌ها اضافه کنید.")
            conn.execute(
                """INSERT INTO admin_roles
                   (user_id, role, updated_by, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     role = excluded.role,
                     updated_by = excluded.updated_by,
                     updated_at = excluded.updated_at""",
                (int(user_id), role, int(updated_by), utcnow()),
            )
        self.audit(updated_by, "admin.role", "admin", user_id, {"role": role})

    def admin_role(self, user_id: int, owner_id: int) -> str:
        if int(user_id) == int(owner_id):
            return "owner"
        with self.connect() as conn:
            row = conn.execute(
                "SELECT role FROM admin_roles WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return str(row["role"]) if row else "manager"

    def can(self, user_id: int, owner_id: int, permission: str) -> bool:
        role = self.admin_role(user_id, owner_id)
        allowed = ADMIN_ROLES.get(role, set())
        return "*" in allowed or permission in allowed

    def dashboard(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today = now.date().isoformat()
        month = now.strftime("%Y-%m")
        with self.connect() as conn:
            result = {
                "users": conn.execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0],
                "blocked_users": conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_active = 0"
                ).fetchone()[0],
                "selfs": conn.execute(
                    """SELECT COUNT(*) FROM users
                       WHERE phone IS NOT NULL AND TRIM(phone) != ''
                         AND session_file IS NOT NULL
                         AND TRIM(session_file) != ''"""
                ).fetchone()[0],
                "enabled_selfs": conn.execute(
                    "SELECT COUNT(*) FROM users WHERE self_enabled = 1"
                ).fetchone()[0],
                "expired_selfs": conn.execute(
                    """SELECT COUNT(*) FROM users
                       WHERE expiration_date IS NOT NULL
                         AND expiration_date != ''
                         AND expiration_date < ?""",
                    (utcnow(),),
                ).fetchone()[0],
                "open_tickets": conn.execute(
                    "SELECT COUNT(*) FROM support_tickets WHERE status != 'closed'"
                ).fetchone()[0],
                "pending_broadcasts": conn.execute(
                    "SELECT COUNT(*) FROM broadcasts WHERE status = 'pending'"
                ).fetchone()[0],
                "errors_today": conn.execute(
                    """SELECT COUNT(*) FROM runtime_events
                       WHERE level IN ('ERROR', 'CRITICAL')
                         AND substr(created_at, 1, 10) = ?""",
                    (today,),
                ).fetchone()[0],
                "revenue_today": conn.execute(
                    """SELECT COALESCE(SUM(amount_toman), 0)
                       FROM payment_receipts
                       WHERE status = 'approved'
                         AND substr(COALESCE(reviewed_at, created_at), 1, 10) = ?""",
                    (today,),
                ).fetchone()[0],
                "revenue_month": conn.execute(
                    """SELECT COALESCE(SUM(amount_toman), 0)
                       FROM payment_receipts
                       WHERE status = 'approved'
                         AND substr(COALESCE(reviewed_at, created_at), 1, 7) = ?""",
                    (month,),
                ).fetchone()[0],
            }
        return {key: int(value or 0) for key, value in result.items()}

    def search_users(self, query: str, limit: int = 12) -> list[sqlite3.Row]:
        term = str(query).strip()
        normalized = term.lstrip("@")
        like = f"%{normalized}%"
        with self.connect() as conn:
            return conn.execute(
                """SELECT user_id, username, first_name, last_name, phone,
                          coins, is_active, expiration_date, self_status
                   FROM users
                   WHERE CAST(user_id AS TEXT) LIKE ?
                      OR COALESCE(username, '') LIKE ?
                      OR COALESCE(first_name, '') LIKE ?
                      OR COALESCE(last_name, '') LIKE ?
                      OR COALESCE(phone, '') LIKE ?
                   ORDER BY updated_at DESC, user_id DESC
                   LIMIT ?""",
                (like, like, like, like, like, int(limit)),
            ).fetchall()

    def set_user_active(
        self, user_id: int, active: bool, admin_id: int
    ) -> None:
        with self.connect() as conn:
            before = conn.execute(
                "SELECT is_active FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            changed = conn.execute(
                """UPDATE users SET is_active = ?, updated_at = ?
                   WHERE user_id = ?""",
                (1 if active else 0, utcnow(), int(user_id)),
            ).rowcount
        if not changed:
            raise LookupError("کاربر پیدا نشد.")
        self.audit(
            admin_id,
            "user.unblock" if active else "user.block",
            "user",
            user_id,
            {
                "before": bool(before["is_active"]) if before else None,
                "after": bool(active),
            },
        )

    def adjust_balance(
        self, user_id: int, amount: int, admin_id: int, note: str
    ) -> int:
        if amount == 0:
            raise ValueError("مبلغ تغییر موجودی نمی‌تواند صفر باشد.")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?", (int(user_id),)
            ).fetchone()
            if not row:
                raise LookupError("کاربر پیدا نشد.")
            previous_balance = int(row["coins"] or 0)
            new_balance = previous_balance + int(amount)
            if new_balance < 0:
                raise ValueError("موجودی کاربر برای این کسر کافی نیست.")
            conn.execute(
                "UPDATE users SET coins = ?, updated_at = ? WHERE user_id = ?",
                (new_balance, utcnow(), int(user_id)),
            )
            conn.execute(
                """INSERT INTO balance_transactions
                   (user_id, amount, balance_after, transaction_type,
                    admin_id, note)
                   VALUES (?, ?, ?, 'admin_adjustment', ?, ?)""",
                (
                    int(user_id),
                    int(amount),
                    new_balance,
                    int(admin_id),
                    str(note)[:300],
                ),
            )
        self.audit(
            admin_id,
            "balance.adjust",
            "user",
            user_id,
            {
                "amount": int(amount),
                "before": previous_balance,
                "after": new_balance,
                "note": str(note)[:300],
            },
        )
        return new_balance

    def list_plans(self, active_only: bool = False) -> list[sqlite3.Row]:
        where = "WHERE is_active = 1" if active_only else ""
        with self.connect() as conn:
            return conn.execute(
                f"""SELECT * FROM subscription_plans {where}
                    ORDER BY is_active DESC, duration_days, id"""
            ).fetchall()

    def get_plan(self, plan_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM subscription_plans WHERE id = ?",
                (int(plan_id),),
            ).fetchone()

    def create_plan(
        self,
        name: str,
        duration_days: int,
        price_coins: int,
        trial_days: int,
        admin_id: int,
    ) -> int:
        clean_name = str(name).strip()[:50]
        if not clean_name:
            raise ValueError("نام پلن خالی است.")
        if not 0 <= duration_days <= 36500:
            raise ValueError("مدت پلن باید بین صفر و ۳۶۵۰۰ روز باشد.")
        if not 0 <= price_coins <= 1_000_000_000:
            raise ValueError("قیمت پلن معتبر نیست.")
        now = utcnow()
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO subscription_plans
                       (name, duration_days, price_coins, trial_days,
                        feature_keys, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        clean_name,
                        int(duration_days),
                        int(price_coins),
                        max(0, int(trial_days)),
                        json.dumps(list(FEATURE_CATALOG), ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                plan_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("پلنی با این نام از قبل وجود دارد.") from exc
        self.audit(admin_id, "plan.create", "plan", plan_id)
        return plan_id

    def toggle_plan(self, plan_id: int, admin_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT is_active FROM subscription_plans WHERE id = ?",
                (int(plan_id),),
            ).fetchone()
            if not row:
                raise LookupError("پلن پیدا نشد.")
            enabled = not bool(row["is_active"])
            conn.execute(
                """UPDATE subscription_plans
                   SET is_active = ?, updated_at = ? WHERE id = ?""",
                (1 if enabled else 0, utcnow(), int(plan_id)),
            )
        self.audit(
            admin_id, "plan.toggle", "plan", plan_id, {"active": enabled}
        )
        return enabled

    def assign_plan(
        self, user_id: int, plan_id: int, admin_id: int, auto_renew: bool = False
    ) -> str | None:
        plan = self.get_plan(plan_id)
        if not plan:
            raise LookupError("پلن پیدا نشد.")
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
        days = int(plan["duration_days"] or 0)
        expires = None if days == 0 else now + timedelta(days=days)
        expires_text = expires.isoformat() if expires else None
        with self.connect() as conn:
            user = conn.execute(
                "SELECT expiration_date FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            if not user:
                raise LookupError("کاربر پیدا نشد.")
            conn.execute(
                """UPDATE user_subscriptions SET status = 'replaced'
                   WHERE user_id = ? AND status = 'active'""",
                (int(user_id),),
            )
            conn.execute(
                """INSERT INTO user_subscriptions
                   (user_id, plan_id, starts_at, expires_at, status,
                    auto_renew, assigned_by, created_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    int(user_id),
                    int(plan_id),
                    now.isoformat(),
                    expires_text,
                    1 if auto_renew else 0,
                    int(admin_id),
                    now.isoformat(),
                ),
            )
            conn.execute(
                """UPDATE users SET expiration_date = ?, is_active = 1,
                   updated_at = ? WHERE user_id = ?""",
                (expires_text, now.isoformat(), int(user_id)),
            )
        self.audit(
            admin_id,
            "subscription.assign",
            "user",
            user_id,
            {
                "plan_id": plan_id,
                "before_expires_at": user["expiration_date"],
                "after_expires_at": expires_text,
                "auto_renew": bool(auto_renew),
            },
        )
        return expires_text

    def active_subscription(self, user_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """SELECT us.*, sp.name AS plan_name, sp.duration_days,
                          sp.price_coins
                   FROM user_subscriptions us
                   JOIN subscription_plans sp ON sp.id = us.plan_id
                   WHERE us.user_id = ? AND us.status = 'active'
                   ORDER BY us.id DESC LIMIT 1""",
                (int(user_id),),
            ).fetchone()

    def create_discount(
        self,
        code: str,
        percent: int,
        max_uses: int,
        expires_at: str | None,
        admin_id: int,
    ) -> int:
        clean = str(code).strip().upper()
        if not clean or not clean.replace("-", "").replace("_", "").isalnum():
            raise ValueError("کد تخفیف فقط می‌تواند شامل حرف، عدد، - و _ باشد.")
        if not 1 <= int(percent) <= 100:
            raise ValueError("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
        if expires_at and parse_datetime(expires_at) is None:
            raise ValueError("تاریخ انقضا معتبر نیست.")
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO discount_codes
                       (code, percent, max_uses, expires_at, is_active,
                        created_by, created_at)
                       VALUES (?, ?, ?, ?, 1, ?, ?)""",
                    (
                        clean,
                        int(percent),
                        max(0, int(max_uses)),
                        expires_at or None,
                        int(admin_id),
                        utcnow(),
                    ),
                )
                code_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("این کد تخفیف قبلاً ساخته شده است.") from exc
        self.audit(admin_id, "discount.create", "discount", code_id)
        return code_id

    def list_discounts(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM discount_codes
                   ORDER BY is_active DESC, id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()

    def set_feature_policy(
        self,
        scope_type: str,
        scope_id: str | int,
        feature_key: str,
        enabled: bool,
        admin_id: int,
    ) -> None:
        if scope_type not in {"global", "plan", "user"}:
            raise ValueError("نوع محدوده معتبر نیست.")
        if feature_key not in FEATURE_CATALOG:
            raise ValueError("قابلیت انتخاب‌شده معتبر نیست.")
        scope = "" if scope_type == "global" else str(scope_id)
        if scope_type != "global" and not scope.isdigit():
            raise ValueError("شناسه محدوده باید عددی باشد.")
        with self.connect() as conn:
            before = conn.execute(
                """SELECT enabled FROM feature_policies
                   WHERE scope_type = ? AND scope_id = ?
                     AND feature_key = ?""",
                (scope_type, scope, feature_key),
            ).fetchone()
            conn.execute(
                """INSERT INTO feature_policies
                   (scope_type, scope_id, feature_key, enabled, updated_by,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scope_type, scope_id, feature_key) DO UPDATE SET
                     enabled = excluded.enabled,
                     updated_by = excluded.updated_by,
                     updated_at = excluded.updated_at""",
                (
                    scope_type,
                    scope,
                    feature_key,
                    1 if enabled else 0,
                    int(admin_id),
                    utcnow(),
                ),
            )
        self.audit(
            admin_id,
            "feature.policy",
            scope_type,
            scope,
            {
                "feature": feature_key,
                "before": bool(before["enabled"]) if before else None,
                "after": bool(enabled),
            },
        )

    def global_feature_states(self) -> dict[str, bool]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT feature_key, enabled FROM feature_policies
                   WHERE scope_type = 'global' AND scope_id = ''"""
            ).fetchall()
        explicit = {str(row["feature_key"]): bool(row["enabled"]) for row in rows}
        return {key: explicit.get(key, True) for key in FEATURE_CATALOG}

    def effective_feature_states(self, user_id: int) -> dict[str, bool]:
        states = self.global_feature_states()
        subscription = self.active_subscription(user_id)
        plan_id = str(subscription["plan_id"]) if subscription else ""
        with self.connect() as conn:
            if plan_id:
                rows = conn.execute(
                    """SELECT feature_key, enabled FROM feature_policies
                       WHERE scope_type = 'plan' AND scope_id = ?""",
                    (plan_id,),
                ).fetchall()
                states.update(
                    {str(row["feature_key"]): bool(row["enabled"]) for row in rows}
                )
            rows = conn.execute(
                """SELECT feature_key, enabled FROM feature_policies
                   WHERE scope_type = 'user' AND scope_id = ?""",
                (str(int(user_id)),),
            ).fetchall()
        states.update(
            {str(row["feature_key"]): bool(row["enabled"]) for row in rows}
        )
        return states

    def apply_features_to_self(
        self, user_id: int, self_db_path: str | Path
    ) -> dict[str, bool]:
        path = Path(self_db_path)
        if not path.exists():
            return self.effective_feature_states(user_id)
        states = self.effective_feature_states(user_id)
        with sqlite3.connect(path, timeout=10) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "settings" not in tables:
                return states
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(settings)")
            }
            if not {"key", "value"}.issubset(columns):
                return states
            for key, enabled in states.items():
                setting_key = FEATURE_CATALOG[key][1]
                conn.execute(
                    """INSERT INTO settings(key, value) VALUES (?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                    (setting_key, "on" if enabled else "off"),
                )
        return states

    def create_broadcast(
        self,
        segment: str,
        body: str,
        scheduled_at: str,
        admin_id: int,
    ) -> int:
        if segment not in {"all", "active", "expired", "blocked"}:
            raise ValueError("گروه هدف ارسال معتبر نیست.")
        if not str(body).strip():
            raise ValueError("متن پیام خالی است.")
        if parse_datetime(scheduled_at) is None:
            raise ValueError("زمان ارسال معتبر نیست.")
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO broadcasts
                   (segment, body, scheduled_at, status, created_by, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (
                    segment,
                    str(body).strip()[:4000],
                    scheduled_at,
                    int(admin_id),
                    utcnow(),
                ),
            )
            broadcast_id = int(cursor.lastrowid)
        self.audit(admin_id, "broadcast.create", "broadcast", broadcast_id)
        return broadcast_id

    def list_broadcasts(self, limit: int = 12) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()

    def claim_due_broadcast(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM broadcasts
                   WHERE status = 'pending' AND cancel_requested = 0
                     AND scheduled_at <= ?
                   ORDER BY scheduled_at, id LIMIT 1""",
                (utcnow(),),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE broadcasts SET status = 'running',
                       started_at = ? WHERE id = ? AND status = 'pending'""",
                    (utcnow(), int(row["id"])),
                )
        return row

    def broadcast_recipients(
        self, segment: str, broadcast_id: int | None = None
    ) -> list[int]:
        conditions = {
            "all": "1 = 1",
            "active": "is_active = 1",
            "blocked": "is_active = 0",
            "expired": (
                "expiration_date IS NOT NULL AND expiration_date != '' "
                "AND datetime(replace(expiration_date, 'T', ' ')) < CURRENT_TIMESTAMP"
            ),
        }
        where = conditions.get(segment, "1 = 0")
        delivery_filter = ""
        parameters: tuple[Any, ...] = ()
        if broadcast_id is not None:
            delivery_filter = (
                " AND NOT EXISTS ("
                "SELECT 1 FROM broadcast_deliveries bd "
                "WHERE bd.broadcast_id = ? AND bd.user_id = users.user_id "
                "AND bd.status = 'sent')"
            )
            parameters = (int(broadcast_id),)
        with self.connect() as conn:
            return [
                int(row[0])
                for row in conn.execute(
                    f"""SELECT user_id FROM users
                        WHERE {where}{delivery_filter} ORDER BY user_id""",
                    parameters,
                ).fetchall()
            ]

    def broadcast_cancel_requested(self, broadcast_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM broadcasts WHERE id = ?",
                (int(broadcast_id),),
            ).fetchone()
        return not row or bool(row[0])

    def cancel_broadcast(self, broadcast_id: int, admin_id: int) -> None:
        with self.connect() as conn:
            changed = conn.execute(
                """UPDATE broadcasts SET cancel_requested = 1,
                   status = CASE WHEN status = 'pending' THEN 'cancelled'
                                 ELSE status END
                   WHERE id = ? AND status IN ('pending', 'running')""",
                (int(broadcast_id),),
            ).rowcount
        if not changed:
            raise LookupError("ارسال فعال یا در انتظاری با این شماره وجود ندارد.")
        self.audit(admin_id, "broadcast.cancel", "broadcast", broadcast_id)

    def record_broadcast_delivery(
        self, broadcast_id: int, user_id: int, success: bool, error_code: str = ""
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO broadcast_deliveries
                   (broadcast_id, user_id, status, error_code, delivered_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(broadcast_id, user_id) DO UPDATE SET
                     status = excluded.status,
                     error_code = excluded.error_code,
                     delivered_at = excluded.delivered_at""",
                (
                    int(broadcast_id),
                    int(user_id),
                    "sent" if success else "failed",
                    str(error_code)[:80] or None,
                    utcnow(),
                ),
            )

    def finish_broadcast(
        self, broadcast_id: int, success: int, failed: int, cancelled: bool
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE broadcasts SET status = ?, completed_at = ?,
                   success_count = ?, failed_count = ? WHERE id = ?""",
                (
                    "cancelled" if cancelled else "completed",
                    utcnow(),
                    int(success),
                    int(failed),
                    int(broadcast_id),
                ),
            )

    def open_support_ticket(self, user_id: int, body: str) -> int:
        message = str(body).strip()
        if not message:
            raise ValueError("متن درخواست پشتیبانی خالی است.")
        now = utcnow()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT id FROM support_tickets
                   WHERE user_id = ? AND status != 'closed'
                   ORDER BY id DESC LIMIT 1""",
                (int(user_id),),
            ).fetchone()
            if row:
                ticket_id = int(row["id"])
                conn.execute(
                    "UPDATE support_tickets SET updated_at = ? WHERE id = ?",
                    (now, ticket_id),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO support_tickets
                       (user_id, subject, status, created_at, updated_at)
                       VALUES (?, 'پشتیبانی', 'open', ?, ?)""",
                    (int(user_id), now, now),
                )
                ticket_id = int(cursor.lastrowid)
            conn.execute(
                """INSERT INTO support_messages
                   (ticket_id, sender_type, sender_id, body, created_at)
                   VALUES (?, 'user', ?, ?, ?)""",
                (ticket_id, int(user_id), message[:4000], now),
            )
        return ticket_id

    def list_tickets(
        self, status: str = "open", limit: int = 15
    ) -> list[sqlite3.Row]:
        where = "WHERE st.status != 'closed'" if status == "open" else ""
        with self.connect() as conn:
            return conn.execute(
                f"""SELECT st.*, u.username, u.first_name,
                           (SELECT body FROM support_messages sm
                            WHERE sm.ticket_id = st.id
                            ORDER BY sm.id DESC LIMIT 1) AS last_message
                    FROM support_tickets st
                    LEFT JOIN users u ON u.user_id = st.user_id
                    {where}
                    ORDER BY st.updated_at DESC, st.id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()

    def ticket(self, ticket_id: int) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
        with self.connect() as conn:
            ticket = conn.execute(
                """SELECT st.*, u.username, u.first_name
                   FROM support_tickets st
                   LEFT JOIN users u ON u.user_id = st.user_id
                   WHERE st.id = ?""",
                (int(ticket_id),),
            ).fetchone()
            messages = conn.execute(
                """SELECT * FROM support_messages WHERE ticket_id = ?
                   ORDER BY id DESC LIMIT 10""",
                (int(ticket_id),),
            ).fetchall()
        return ticket, list(reversed(messages))

    def reply_ticket(self, ticket_id: int, admin_id: int, body: str) -> int:
        message = str(body).strip()
        if not message:
            raise ValueError("پاسخ خالی است.")
        now = utcnow()
        with self.connect() as conn:
            ticket = conn.execute(
                "SELECT user_id, status FROM support_tickets WHERE id = ?",
                (int(ticket_id),),
            ).fetchone()
            if not ticket:
                raise LookupError("تیکت پیدا نشد.")
            if ticket["status"] == "closed":
                raise ValueError("این تیکت بسته شده است.")
            conn.execute(
                """INSERT INTO support_messages
                   (ticket_id, sender_type, sender_id, body, created_at)
                   VALUES (?, 'admin', ?, ?, ?)""",
                (int(ticket_id), int(admin_id), message[:4000], now),
            )
            conn.execute(
                """UPDATE support_tickets SET status = 'answered',
                   assigned_admin_id = ?, updated_at = ? WHERE id = ?""",
                (int(admin_id), now, int(ticket_id)),
            )
        self.audit(admin_id, "support.reply", "ticket", ticket_id)
        return int(ticket["user_id"])

    def close_ticket(self, ticket_id: int, admin_id: int) -> int:
        now = utcnow()
        with self.connect() as conn:
            ticket = conn.execute(
                "SELECT user_id FROM support_tickets WHERE id = ?",
                (int(ticket_id),),
            ).fetchone()
            if not ticket:
                raise LookupError("تیکت پیدا نشد.")
            conn.execute(
                """UPDATE support_tickets SET status = 'closed',
                   closed_at = ?, updated_at = ? WHERE id = ?""",
                (now, now, int(ticket_id)),
            )
        self.audit(admin_id, "support.close", "ticket", ticket_id)
        return int(ticket["user_id"])

    def upsert_force_join_channel(
        self,
        chat_id: str | int,
        username: str,
        title: str,
        join_url: str,
        admin_id: int,
    ) -> int:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO force_join_channels
                   (chat_id, username, title, join_url, is_active,
                    created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                     username = excluded.username,
                     title = excluded.title,
                     join_url = excluded.join_url,
                     is_active = 1,
                     updated_at = excluded.updated_at""",
                (
                    str(chat_id),
                    str(username).lstrip("@"),
                    str(title).strip()[:100],
                    str(join_url).strip(),
                    int(admin_id),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM force_join_channels WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchone()
        channel_id = int(row["id"])
        self.audit(admin_id, "join.upsert", "channel", channel_id)
        return channel_id

    def active_force_join_channels(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM force_join_channels WHERE is_active = 1
                   ORDER BY id"""
            ).fetchall()

    def list_force_join_channels(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM force_join_channels ORDER BY id"
            ).fetchall()

    def toggle_force_join_channel(
        self, channel_id: int, admin_id: int
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT is_active FROM force_join_channels WHERE id = ?",
                (int(channel_id),),
            ).fetchone()
            if not row:
                raise LookupError("کانال پیدا نشد.")
            active = not bool(row["is_active"])
            conn.execute(
                """UPDATE force_join_channels SET is_active = ?,
                   updated_at = ? WHERE id = ?""",
                (1 if active else 0, utcnow(), int(channel_id)),
            )
        self.audit(
            admin_id, "join.toggle", "channel", channel_id, {"active": active}
        )
        return active

    def delete_force_join_channel(
        self, channel_id: int, admin_id: int
    ) -> None:
        with self.connect() as conn:
            changed = conn.execute(
                "DELETE FROM force_join_channels WHERE id = ?",
                (int(channel_id),),
            ).rowcount
        if not changed:
            raise LookupError("کانال پیدا نشد.")
        self.audit(admin_id, "join.delete", "channel", channel_id)

    def recent_audit(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM admin_audit_log
                   ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()

    def recent_events(self, limit: int = 15) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM runtime_events ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()

    @staticmethod
    def _serialize_sqlite(path: Path) -> bytes:
        with sqlite3.connect(path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout = 10000")
            try:
                return connection.serialize()
            except AttributeError:
                # Python 3.11 on supported cPanel builds provides serialize().
                # This branch keeps a clear failure instead of writing temp files.
                raise RuntimeError("SQLite serialize روی این نسخه Python پشتیبانی نمی‌شود.")

    def create_backup(self, admin_id: int) -> dict[str, Any]:
        """Build a complete encrypted backup in memory; never persist ZIPs."""
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d-%H%M%S")
        filename = f"selfbot-cloud-backup-{timestamp}.zip"
        output = io.BytesIO()
        manifest = {
            "format": 2,
            "created_at": utcnow(),
            "storage": "telegram-cloud-only",
            "files": [],
        }
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            users_bytes = self._serialize_sqlite(self.users_db)
            archive.writestr("data/users.db", users_bytes)
            manifest["files"].append("data/users.db")

            # Per-account settings databases.  No media folders are included.
            for path in sorted(self.data_dir.glob("bot_data_*.db")):
                try:
                    archive.writestr(
                        f"data/{path.name}", self._serialize_sqlite(path)
                    )
                    manifest["files"].append(f"data/{path.name}")
                except sqlite3.Error:
                    continue

            key_path = self.data_dir / ".session.key"
            if key_path.is_file():
                archive.writestr("data/.session.key", key_path.read_bytes())
                manifest["files"].append("data/.session.key")

            for path in sorted(self.sessions_dir.glob("session_*.txt")):
                archive.writestr(f"sessions/{path.name}", path.read_bytes())
                manifest["files"].append(f"sessions/{path.name}")

            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        payload = output.getvalue()
        self.audit(
            admin_id, "backup.create.cloud", "backup", 0,
            {"filename": filename, "size_bytes": len(payload)},
        )
        return {
            "id": 0,
            "filename": filename,
            "size_bytes": len(payload),
            "content": payload,
            "created_at": utcnow(),
        }

    def list_backups(self, limit: int = 10) -> list[sqlite3.Row]:
        # Backups are intentionally not retained on the host.
        return []

    def restore_backup(self, backup_id: int, admin_id: int) -> None:
        raise RuntimeError(
            "بکاپ روی هاست نگهداری نمی‌شود؛ فایل بکاپ را از تلگرام بارگذاری کنید."
        )


def normalize_segment(value: str) -> str:
    mapping = {
        "all": "all",
        "همه": "all",
        "active": "active",
        "فعال": "active",
        "expired": "expired",
        "منقضی": "expired",
        "blocked": "blocked",
        "مسدود": "blocked",
    }
    return mapping.get(str(value).strip().lower(), "")


def chunks(items: Iterable[int], size: int) -> Iterable[list[int]]:
    batch: list[int] = []
    for item in items:
        batch.append(int(item))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
