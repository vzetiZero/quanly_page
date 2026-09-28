"""Lịch đăng video hàng loạt theo khung giờ (scheduler chạy nền).

Ý tưởng: hàng đợi đã có cột "Thời gian đăng" nhưng trước đây phải mở app,
bấm "Đăng" rồi đứng chờ. Ở đây lịch được lưu vào SQLite và một luồng nền
cứ mỗi `TICK_SECONDS` quét xem lịch nào đã tới giờ thì đẩy vào đúng luồng
đăng sẵn có (`PostService.run_config_post_queue`) — nên vẫn giữ nguyên toàn bộ
logic retry / proxy / chống đăng trùng / ghi lịch sử.

Hai chế độ lên lịch:
  * ``interval``: cách đều N phút kể từ thời điểm bắt đầu.
  * ``slots``: các khung giờ cố định trong ngày (vd 09:00 / 14:00 / 20:00),
    lặp lại qua các ngày sau tới khi đủ số video.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("facebook_ui")

MODE_INTERVAL = "interval"
MODE_SLOTS = "slots"

TICK_SECONDS = 20
# Trần số ngày được quét để tìm khung giờ phù hợp (tránh vòng lặp vô hạn).
MAX_HORIZON_DAYS = 366


def parse_hhmm(value: Any) -> Optional[Tuple[int, int]]:
    """Đổi "09:30" / "9.30" thành (9, 30), trả None nếu không hợp lệ."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.replace(":", ".").split(".")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def format_slots(slots: Sequence[datetime]) -> str:
    return ", ".join(slot.strftime("%d/%m %H:%M") for slot in slots[:4])


def build_slots(
    count: int,
    start_at: Optional[datetime] = None,
    mode: str = MODE_INTERVAL,
    interval_minutes: int = 30,
    times_of_day: Optional[Sequence[Any]] = None,
    daily_limit: int = 0,
) -> List[datetime]:
    """Sinh tối đa ``count`` mốc thời gian tăng dần.

    - ``interval``: bắt đầu từ ``start_at``, mỗi lần cách nhau
      ``interval_minutes`` phút.
    - ``slots``: đi qua từng khung giờ trong ngày, sang ngày hôm sau khi hết
      khung. Khung giờ đã trôi qua rồi thì bỏ qua. ``daily_limit > 0`` giới hạn
      số lần đăng mỗi ngày.
    """
    count = max(0, int(count or 0))
    if not count:
        return []
    start = start_at or datetime.now()

    if mode != MODE_SLOTS:
        step = max(1, int(interval_minutes or 1))
        return [start + timedelta(minutes=step * index) for index in range(count)]

    slots = sorted({parsed for parsed in (parse_hhmm(v) for v in (times_of_day or [])) if parsed})
    if not slots:
        return []
    limit = max(0, int(daily_limit or 0))
    result: List[datetime] = []
    day = start.date()
    for _ in range(MAX_HORIZON_DAYS):
        used_today = 0
        for hour, minute in slots:
            if limit and used_today >= limit:
                break
            moment = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
            if moment <= start:
                continue
            result.append(moment)
            if len(result) >= count:
                return result
            used_today += 1
        day += timedelta(days=1)
    return result


def row_to_schedule_row(row: Dict[str, Any], slot: datetime) -> Dict[str, Any]:
    """Chuyển 1 dòng của bảng hàng đợi sang dòng lịch đăng."""
    return {
        "page_id": str(row.get("page_id") or ""),
        "page_name": str(row.get("page_name") or row.get("page") or "").strip(),
        "title": str(row.get("title") or ""),
        "description": str(row.get("description") or ""),
        "video_path": str(row.get("video_path") or ""),
        "comment_text": str(row.get("comment_text") or row.get("comment") or ""),
        "comment_image_paths": str(row.get("comment_image_paths") or row.get("comment_images") or ""),
        "post_type": str(row.get("post_type") or "video"),
        "schedule_time": slot.isoformat(),
    }


class ScheduleService:
    """Lưu lịch đăng + chạy nền để đăng đúng giờ."""

    def __init__(self, schedule_repo: Any, post_service: Any, base_url: str = "") -> None:
        self._repo = schedule_repo
        self._post_service = post_service
        self._base_url = base_url
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._get_pages: Optional[Callable[[], List[Dict[str, Any]]]] = None
        self._concurrency: Dict[str, Any] = {}
        self._on_event: Optional[Callable[[Dict[str, Any]], None]] = None
        self._dispatching = False
        self.last_error = ""

    # ── Truy vấn ───────────────────────────────────────────────────

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def is_dispatching(self) -> bool:
        return self._dispatching

    def pending_count(self) -> int:
        return self._repo.count_scheduled_posts(["pending"])

    def load_pending(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._repo.load_pending_scheduled_posts(limit)

    # ── Lên lịch ───────────────────────────────────────────────────

    def plan_rows(self, rows: List[Dict[str, Any]], plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Gán giờ cho các dòng hàng đợi đủ điều kiện rồi lưu vào DB."""
        candidates = [
            row for row in rows
            if str(row.get("page_name") or row.get("page") or "").strip()
            and str(row.get("video_path") or "").strip()
        ]
        slots = build_slots(
            len(candidates),
            plan.get("start_at"),
            plan.get("mode", MODE_INTERVAL),
            int(plan.get("interval_minutes") or 30),
            plan.get("times_of_day"),
            int(plan.get("daily_limit") or 0),
        )
        if not slots:
            return []
        payload = [row_to_schedule_row(row, slot) for row, slot in zip(candidates, slots)]
        return self._repo.save_scheduled_posts(payload)

    def clear_pending(self) -> int:
        return self._repo.clear_scheduled_posts(["pending"])

    # ── Vòng lặp nền ───────────────────────────────────────────────

    def start(
        self,
        get_pages: Callable[[], List[Dict[str, Any]]],
        concurrency: Optional[Dict[str, Any]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        with self._lock:
            if self.is_running():
                return False
            self._get_pages = get_pages
            self._concurrency = dict(concurrency or {})
            self._on_event = on_event
            self._stop_event.clear()
            # Lịch kẹt ở 'running' (app bị tắt giữa chừng) trả về chờ đăng.
            try:
                revived = self._repo.reset_running_scheduled_posts()
                if revived:
                    logger.info("Scheduler: đưa %d lịch kẹt về chờ đăng", revived)
            except Exception:
                logger.exception("Scheduler: không đọc lại được lịch kẹt")
            self._thread = threading.Thread(target=self._loop, name="schedule-runner", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        # Để những lô đang chạy dừng sớm thay vì đợi hết.
        self._post_service.stop_requested = True

    def _loop(self) -> None:
        logger.info("Scheduler: bắt đầu theo dõi lịch đăng")
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Scheduler: lỗi trong một nhịp: %s", exc)
            self._stop_event.wait(TICK_SECONDS)
        self._dispatching = False
        logger.info("Scheduler: đã dừng")

    def _tick(self) -> None:
        if self._stop_event.is_set():
            return
        if getattr(self._post_service, "is_busy", False):
            return
        due = self._repo.load_due_scheduled_posts(datetime.now().isoformat())
        if not due:
            return
        self._dispatching = True
        try:
            self._dispatch(due)
        finally:
            self._dispatching = False

    def _dispatch(self, due: List[Dict[str, Any]]) -> None:
        now = datetime.now()
        rows = [
            {
                "schedule_id": int(item["id"]),
                "page_id": str(item.get("page_id") or ""),
                "page_name": str(item.get("page_name") or ""),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "video_path": str(item.get("video_path") or ""),
                # Đã tới giờ nên không chờ thêm (để tránh đọc nhầm ISO).
                "schedule_time": "",
                "comment_text": str(item.get("comment_text") or ""),
                "comment_image_paths": str(item.get("comment_image_paths") or ""),
                "post_type": str(item.get("post_type") or "video"),
            }
            for item in due
        ]
        pages: List[Dict[str, Any]] = []
        if callable(self._get_pages):
            try:
                pages = self._get_pages() or []
            except Exception:
                logger.exception("Scheduler: không lấy được danh sách page")
        if not pages:
            # Chưa đánh dấu "running" để các lịch này vẫn còn chờ, không bị kẹt.
            logger.warning("Scheduler: chưa có page nào trong danh sách, bỏ qua lô này")
            self._emit("warning", text="Chưa có page nào trong danh sách — hãy nạp page trước khi bật lịch.")
            return

        self._repo.mark_scheduled_dispatched([row["schedule_id"] for row in rows], now.isoformat())
        self._emit("dispatched", count=len(rows), names=[row["page_name"] for row in rows])

        counters = {"success": 0, "failed": 0, "skipped": 0}
        self._post_service.stop_requested = False
        self._post_service.run_config_post_queue(
            rows=rows,
            pages=pages,
            base_url=self._base_url,
            concurrency_enabled=bool(self._concurrency.get("enabled", True)),
            concurrency_threads=max(1, int(self._concurrency.get("threads", 3) or 3)),
            concurrency_delay=float(self._concurrency.get("delay", 0) or 0),
            on_status=self._on_status,
            on_config_status=self._on_config_status,
            on_link=self._on_link,
            on_complete=lambda *args: None,
        )

        for row in rows:
            result = row.get("result") or {}
            status = str(result.get("status") or "failed")
            if status not in counters:
                # Bị Dừng giữa chừng -> trả về chờ để nhịp sau thử lại.
                status = "pending"
            else:
                counters[status] += 1
            link = str(result.get("permalink_url") or result.get("link") or "")
            error = str(result.get("error") or "")
            posted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "success" else ""
            try:
                self._repo.update_scheduled_result(row["schedule_id"], status, link, error, posted_at)
            except Exception:
                logger.exception("Scheduler: không ghi được kết quả lịch id=%s", row["schedule_id"])

        logger.info("Scheduler: xong lô %d video", len(rows))
        self._emit("done", **counters)

    # ── Bridge từ luồng đăng sang giao diện ─────────────────────────

    def _on_status(self, page_name: str, status: str, detail: str = "") -> None:
        if status == "Thất bại":
            logger.error("Lịch đăng | page=%s | %s | %s", page_name, status, detail)
        else:
            logger.info("Lịch đăng | page=%s | %s | %s", page_name, status, detail)
        self._emit("info", text=f"{page_name}: {status}" + (f" - {detail}" if detail else ""))

    def _on_config_status(self, page_name: str, status: str, page_id: str = "") -> None:
        self._emit("status", page_id=str(page_id or ""), page_name=str(page_name or ""), status=str(status or ""))

    def _on_link(self, page_name: str, link: str, page_id: str = "") -> None:
        self._emit("link", page_name=str(page_name or ""), page_id=str(page_id or ""), link=str(link or ""))

    def _emit(self, kind: str, **payload: Any) -> None:
        if not callable(self._on_event):
            return
        try:
            self._on_event({"type": kind, **payload})
        except Exception:
            logger.exception("Scheduler: không cập nhật được giao diện (%s)", kind)
