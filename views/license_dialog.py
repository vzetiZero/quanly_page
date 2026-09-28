import re
from typing import Optional

from PyQt5 import QtCore, QtWidgets


class TrialLicenseDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kích hoạt bản dùng thử")
        self.resize(380, 190)
        self.setModal(True)
        self.code = ""
        self.setStyleSheet(
            """
            * { font-family: "Nunito"; font-size: 9pt; }
            QDialog { background: #f8fafc; color: #0f172a; }
            QLabel { color: #334155; font-weight: 700; }
            QLineEdit { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 6px; padding: 4px 6px; font-weight: 600; }
            QPushButton { background: #0284c7; color: #ffffff; border: 1px solid #0284c7; border-radius: 6px; padding: 4px 10px; min-height: 22px; font-weight: 700; }
            QPushButton:hover { background: #0ea5e9; }
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addWidget(QtWidgets.QLabel("Nhập mã kích hoạt để sử dụng ứng dụng trong thời gian dùng thử."))
        layout.addWidget(QtWidgets.QLabel(""))

        self.code_input = QtWidgets.QLineEdit()
        self.code_input.setPlaceholderText("Nhập mã:")
        layout.addWidget(self.code_input)

        self.error_label = QtWidgets.QLabel("")
        self.error_label.setStyleSheet("color: #f87171;")
        layout.addWidget(self.error_label)

        buttons = QtWidgets.QHBoxLayout()
        self.ok_btn = QtWidgets.QPushButton("Xác nhận")
        self.ok_btn.clicked.connect(self._validate)
        buttons.addWidget(self.ok_btn)
        self.cancel_btn = QtWidgets.QPushButton("Đóng")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

    def _validate(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            self.error_label.setText("Vui lòng nhập mã")
            return
        if re.fullmatch(r"\s*(\d+)\s*([dD])\s*", code) or code.strip().lower() == "vietboss1998":
            self.code = code
            self.accept()
        else:
            self.error_label.setText("Mã không hợp lệ.")
