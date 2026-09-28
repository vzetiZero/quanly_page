import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class DatabaseManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.init_database()

    def init_database(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    token TEXT PRIMARY KEY,
                    token_prefix TEXT,
                    last_checked TEXT,
                    is_valid INTEGER,
                    expires_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pages (
                    token TEXT,
                    page_id TEXT,
                    page_name TEXT,
                    page_access_token TEXT,
                    account_label TEXT,
                    page_type TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (token, page_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    token TEXT PRIMARY KEY,
                    token_prefix TEXT,
                    account_id TEXT,
                    account_name TEXT,
                    account_label TEXT,
                    last_checked TEXT,
                    is_valid INTEGER,
                    expires_at TEXT,
                    page_count INTEGER,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_media_rotation (
                    page_key TEXT PRIMARY KEY,
                    next_video_index INTEGER,
                    last_video_path TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recent_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_name TEXT,
                    content TEXT,
                    post_type TEXT,
                    posted_at TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_video_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_key TEXT NOT NULL,
                    page_name TEXT,
                    video_path TEXT NOT NULL,
                    video_name TEXT,
                    posted_at TEXT,
                    post_id TEXT,
                    permalink_url TEXT,
                    status TEXT,
                    UNIQUE(page_key, video_path, status)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT NOT NULL,
                    page_name TEXT,
                    category TEXT,
                    about TEXT,
                    description TEXT,
                    phone TEXT,
                    website TEXT,
                    location TEXT,
                    emails TEXT,
                    fan_count INTEGER,
                    followers_count INTEGER,
                    likes_count INTEGER,
                    talking_about_count INTEGER,
                    were_here_count INTEGER,
                    overall_star_rating REAL,
                    rating_count INTEGER,
                    cover_url TEXT,
                    logo_url TEXT,
                    verification_status TEXT,
                    is_published INTEGER,
                    link TEXT,
                    created_time TEXT,
                    instagram_business_account_id TEXT,
                    instagram_business_account_username TEXT,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(page_id, fetched_at)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_page_details_page_id ON page_details(page_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_page_details_fetched_at ON page_details(fetched_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_stats (
                    page_key TEXT PRIMARY KEY,
                    page_name TEXT,
                    video_count INTEGER DEFAULT 0,
                    video_views INTEGER,
                    followers_count INTEGER,
                    fan_count INTEGER,
                    updated_at TEXT
                )
                """
            )
            conn.commit()

    # ── Token operations ──────────────────────────────────────────

    def save_token_state(self, token: str, is_valid: bool, expires_at: Optional[str], error: Optional[str]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tokens (token, token_prefix, last_checked, is_valid, expires_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    token_prefix=excluded.token_prefix,
                    last_checked=excluded.last_checked,
                    is_valid=excluded.is_valid,
                    expires_at=excluded.expires_at,
                    error=excluded.error
                """,
                (token, token[:12], datetime.now().isoformat(), 1 if is_valid else 0, expires_at, error),
            )
            conn.commit()

    # ── Account operations ────────────────────────────────────────

    def save_account_state(
        self,
        token: str,
        is_valid: bool,
        expires_at: Optional[str],
        error: Optional[str],
        account_id: Optional[str] = None,
        account_name: Optional[str] = None,
        page_count: int = 0,
    ) -> None:
        label = (account_name or token[:12] or "").strip()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    token, token_prefix, account_id, account_name, account_label,
                    last_checked, is_valid, expires_at, page_count, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    token_prefix=excluded.token_prefix,
                    account_id=excluded.account_id,
                    account_name=excluded.account_name,
                    account_label=excluded.account_label,
                    last_checked=excluded.last_checked,
                    is_valid=excluded.is_valid,
                    expires_at=excluded.expires_at,
                    page_count=excluded.page_count,
                    error=excluded.error
                """,
                (token, token[:12], account_id, account_name, label, datetime.now().isoformat(), 1 if is_valid else 0, expires_at, page_count, error),
            )
            conn.commit()

    def load_all_accounts(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT token_prefix, account_name, account_id, is_valid, expires_at, page_count, last_checked, error
                FROM accounts ORDER BY last_checked DESC, account_name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            {
                "token_prefix": r[0] or "", "account_name": r[1] or "", "account_id": r[2] or "",
                "is_valid": r[3], "expires_at": r[4] or "", "page_count": r[5] or 0,
                "last_checked": r[6] or "", "error": r[7] or "",
            }
            for r in rows
        ]

    # ── Page operations ───────────────────────────────────────────

    def save_pages(self, token: str, pages: List[Dict[str, Any]]) -> None:
        timestamp = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            for page in pages:
                conn.execute(
                    """
                    INSERT INTO pages (token, page_id, page_name, page_access_token, account_label, page_type, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(token, page_id) DO UPDATE SET
                        page_name=excluded.page_name,
                        page_access_token=excluded.page_access_token,
                        account_label=excluded.account_label,
                        page_type=excluded.page_type,
                        last_updated=excluded.last_updated
                    """,
                    (
                        token, page.get("id"), page.get("name"), page.get("access_token", ""),
                        token[:12], page.get("page_type", "me/accounts"), timestamp,
                    ),
                )
            conn.commit()

    def load_cached_pages(self, token: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT page_id, page_name, page_access_token, account_label, page_type, last_updated FROM pages WHERE token = ?",
                (token,),
            ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "access_token": r[2],
                "account_label": r[3] or token[:12], "page_type": r[4],
                "last_updated": r[5], "status": "Cache",
            }
            for r in rows
        ]

    def load_all_cached_pages(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT token, page_id, page_name, page_access_token, account_label, page_type, last_updated
                FROM pages ORDER BY last_updated DESC, page_name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            {
                "account_token": r[0], "id": r[1], "name": r[2], "access_token": r[3],
                "account_label": r[4] or str(r[0] or "")[:12], "page_type": r[5],
                "last_updated": r[6], "status": "Cache",
            }
            for r in rows
        ]

    def delete_pages_for_token(self, token: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pages WHERE token = ?", (token,))
            conn.commit()

    # ── Media rotation ────────────────────────────────────────────

    def load_rotation_index(self, rotation_key: str) -> int:
        if not rotation_key:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT next_video_index FROM page_media_rotation WHERE page_key = ?",
                (rotation_key,),
            ).fetchone()
        if not row or row[0] is None:
            return 0
        try:
            return max(0, int(row[0]))
        except Exception:
            return 0

    def save_rotation_index(self, rotation_key: str, next_video_index: int, last_item_path: str = "") -> None:
        if not rotation_key:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO page_media_rotation (page_key, next_video_index, last_video_path, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(page_key) DO UPDATE SET
                    next_video_index=excluded.next_video_index,
                    last_video_path=excluded.last_video_path,
                    updated_at=excluded.updated_at
                """,
                (rotation_key, max(0, next_video_index), last_item_path, datetime.now().isoformat()),
            )
            conn.commit()

    # ── Recent posts ──────────────────────────────────────────────

    def log_successful_post(self, page_name: str, content: str, post_type: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO recent_posts (page_name, content, post_type, posted_at, status) VALUES (?, ?, ?, ?, ?)",
                (page_name, content[:400], post_type, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Thành công"),
            )
            conn.commit()

    def load_recent_posts(self, offset: int, limit: int) -> List[Dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM recent_posts").fetchone()[0]
            rows = conn.execute(
                "SELECT page_name, content, post_type, posted_at FROM recent_posts ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [{"page_name": r[0] or "", "content": r[1] or "", "post_type": r[2] or "", "posted_at": r[3] or ""} for r in rows], total

    def clear_recent_posts(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM recent_posts")
            conn.commit()

    # ── Video history ─────────────────────────────────────────────

    def load_successful_video_paths_for_page(self, page_key: str) -> set:
        if not page_key:
            return set()
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT video_path FROM page_video_history WHERE page_key = ? AND status = ?",
                    (page_key, "success"),
                ).fetchall()
            return {str(row[0]) for row in rows if row and row[0]}
        except Exception:
            return set()

    def record_page_video(self, page_key: str, page_name: str, video_path: str, post_id: str = "", permalink_url: str = "") -> None:
        if not page_key or not video_path:
            return
        try:
            resolved = str(Path(video_path).expanduser().resolve())
        except Exception:
            resolved = str(video_path)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO page_video_history
                    (page_key, page_name, video_path, video_name, posted_at, post_id, permalink_url, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (page_key, page_name, resolved, Path(resolved).name, datetime.now().isoformat(), post_id, permalink_url, "success"),
                )
                conn.commit()
        except Exception:
            pass

    def load_posted_video_names_for_page(self, page_key: str) -> set:
        """Trả về tập tên video (basename) đã đăng thành công cho 1 page.

        Dùng TÊN VIDEO làm khoá kiểm soát để tránh đăng lại, kể cả khi
        đường dẫn/thư mục thay đổi.
        """
        if not page_key:
            return set()
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT video_name, video_path FROM page_video_history WHERE page_key = ? AND status = ?",
                    (str(page_key), "success"),
                ).fetchall()
        except Exception:
            return set()
        names = set()
        for video_name, video_path in rows:
            if video_name:
                names.add(str(video_name))
            elif video_path:
                names.add(Path(str(video_path)).name)
        return names

    def load_video_history(self, page_keys: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Lịch sử video đã đăng, lọc theo danh sách page_key (tên page) nếu có."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                if page_keys:
                    keys = [str(k) for k in page_keys if k]
                    if not keys:
                        return []
                    placeholders = ",".join("?" for _ in keys)
                    rows = conn.execute(
                        f"""
                        SELECT page_name, video_name, video_path, posted_at, permalink_url
                        FROM page_video_history
                        WHERE status = 'success' AND page_key IN ({placeholders})
                        ORDER BY posted_at DESC
                        """,
                        keys,
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT page_name, video_name, video_path, posted_at, permalink_url
                        FROM page_video_history
                        WHERE status = 'success'
                        ORDER BY posted_at DESC
                        """
                    ).fetchall()
        except Exception:
            return []
        result: List[Dict[str, Any]] = []
        for page_name, video_name, video_path, posted_at, permalink_url in rows:
            name = video_name or (Path(str(video_path)).name if video_path else "")
            result.append({
                "page_name": page_name or "",
                "video_name": name,
                "posted_at": str(posted_at or "")[:19].replace("T", " "),
                "permalink_url": permalink_url or "",
            })
        return result

    # ── Page stats (thống kê video / view) ────────────────────────

    def count_posted_videos_by_page(self) -> Dict[str, int]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT page_key, COUNT(*) FROM page_video_history WHERE status = 'success' GROUP BY page_key"
                ).fetchall()
        except Exception:
            return {}
        return {str(r[0]): int(r[1]) for r in rows if r and r[0]}

    def load_posted_video_ids(self, page_key: str) -> List[Dict[str, str]]:
        if not page_key:
            return []
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT post_id, permalink_url, video_name
                    FROM page_video_history
                    WHERE page_key = ? AND status = 'success' AND post_id IS NOT NULL AND post_id != ''
                    """,
                    (str(page_key),),
                ).fetchall()
        except Exception:
            return []
        return [{"post_id": str(r[0]), "permalink_url": r[1] or "", "video_name": r[2] or ""} for r in rows]

    def save_page_stats(
        self,
        page_key: str,
        page_name: str,
        video_count: int = 0,
        video_views: Optional[int] = None,
        followers_count: Optional[int] = None,
        fan_count: Optional[int] = None,
    ) -> None:
        if not page_key:
            return
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO page_stats (page_key, page_name, video_count, video_views, followers_count, fan_count, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(page_key) DO UPDATE SET
                        page_name=excluded.page_name,
                        video_count=excluded.video_count,
                        video_views=excluded.video_views,
                        followers_count=excluded.followers_count,
                        fan_count=excluded.fan_count,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(page_key), page_name, int(video_count or 0),
                        video_views, followers_count, fan_count, datetime.now().isoformat(),
                    ),
                )
                conn.commit()
        except Exception:
            pass

    def load_all_page_stats(self) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM page_stats ORDER BY page_name COLLATE NOCASE ASC").fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]

    # ── Page Details (MỚI) ────────────────────────────────────────

    def save_page_details(self, page_id: str, details: Dict[str, Any]) -> None:
        fetched_at = details.get("fetched_at") or datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO page_details (
                    page_id, page_name, category, about, description, phone, website,
                    location, emails, fan_count, followers_count, likes_count,
                    talking_about_count, were_here_count, overall_star_rating, rating_count,
                    cover_url, logo_url, verification_status, is_published, link,
                    created_time, instagram_business_account_id,
                    instagram_business_account_username, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_id, details.get("page_name", ""), details.get("category", ""),
                    details.get("about", ""), details.get("description", ""),
                    details.get("phone", ""), details.get("website", ""),
                    details.get("location", ""), details.get("emails", ""),
                    details.get("fan_count"), details.get("followers_count"),
                    details.get("likes_count"), details.get("talking_about_count"),
                    details.get("were_here_count"), details.get("overall_star_rating"),
                    details.get("rating_count"), details.get("cover_url", ""),
                    details.get("logo_url", ""), details.get("verification_status", ""),
                    1 if details.get("is_published") else 0, details.get("link", ""),
                    details.get("created_time", ""),
                    details.get("instagram_business_account_id", ""),
                    details.get("instagram_business_account_username", ""),
                    fetched_at,
                ),
            )
            conn.commit()

    def load_latest_page_detail(self, page_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM page_details WHERE page_id = ? ORDER BY fetched_at DESC LIMIT 1",
                (page_id,),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def load_page_details_history(self, page_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM page_details WHERE page_id = ? ORDER BY fetched_at DESC LIMIT ?",
                (page_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Bulk cache clear ──────────────────────────────────────────

    def clear_all_cache(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pages")
            conn.execute("DELETE FROM tokens")
            conn.execute("DELETE FROM accounts")
            conn.execute("DELETE FROM page_media_rotation")
            conn.commit()
