import os
from typing import Optional

from PyQt5 import QtCore, QtWidgets


class LogTab(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QtWidgets.QLabel("Nhật ký chi tiết cho từng lần đăng, kèm trạng thái và thông tin phản hồi từ Facebook")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #334155; font-weight: 650;")
        layout.addWidget(hint)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Đợi quá trình đăng để xem log...")
        self.log_output.setMaximumBlockCount(2000)
        layout.addWidget(self.log_output)

    def append_log(self, message: str, level: str = "info") -> None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = {"info": "INFO", "warning": "WARN", "error": "ERROR"}.get(level.lower(), "INFO")
        line = f"{timestamp} [{prefix}] {message}"
        self.log_output.appendPlainText(line)
        self.log_output.ensureCursorVisible()

    def load_history(self, file_path: str, max_lines: int = 500) -> None:
        """Nạp N dòng cuối của file log cũ để tab không trống khi mở app.

        Dòng trong file đã có sẵn dấu thời gian nên thêm nguyên văn, không
        prefix lại như ``append_log``.
        """
        try:
            path = os.fspath(file_path)
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
            for line in lines[-max_lines:]:
                self.log_output.appendPlainText(line)
            self.log_output.ensureCursorVisible()
        except Exception:
            pass
