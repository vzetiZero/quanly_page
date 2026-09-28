from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

# ── Thứ tự cột bảng page (Chọn lên đầu) ───────────────────────────
PAGE_COL_SELECT = 0
PAGE_COL_INDEX = 1
PAGE_COL_NAME = 2
PAGE_COL_TOKEN_STATUS = 3
PAGE_COL_POST_STATUS = 4
PAGE_COL_ACCOUNT = 5
PAGE_COL_ID = 6
PAGE_COL_OPEN = 7
PAGE_COL_ACCESS_TOKEN = 8
PAGE_COL_INFO = 9

PAGE_HEADERS = [
    "Chọn", "STT", "Tên Page", "Trạng thái token", "Trạng thái đăng",
    "Tài khoản", "Page ID", "Open trang", "Page Access Token", "Follow/View",
]


def chunked_batches(items: List[Any], size: int) -> List[List[Any]]:
    if size <= 1:
        return [list(items)]
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


class SelectablePageTable(QtWidgets.QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_selecting = False
        self._drag_last_row: Optional[int] = None
        self._drag_base_state: Optional[bool] = None
        self._drag_moved = False
        self.window_ref: Any = None

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            row = self.rowAt(event.pos().y())
            col = self.columnAt(event.pos().x())
            if row >= 0 and col == PAGE_COL_SELECT:
                self._drag_selecting = True
                self._drag_last_row = row
                self._drag_base_state = self._is_row_selected(row)
                self._drag_moved = False
                self.setFocus()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_selecting:
            row = self.rowAt(event.pos().y())
            col = self.columnAt(event.pos().x())
            if row >= 0 and col == PAGE_COL_SELECT and row != self._drag_last_row:
                self._apply_toggle_drag_selection(row)
                self._drag_moved = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._drag_selecting:
            if not self._drag_moved and self._drag_last_row is not None:
                self._toggle_row_selection(self._drag_last_row)
            self._drag_selecting = False
            self._drag_last_row = None
            self._drag_base_state = None
            self._drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _is_row_selected(self, row: int) -> bool:
        item = self.item(row, PAGE_COL_SELECT)
        return item is not None and item.checkState() == QtCore.Qt.Checked

    def _set_row_selection(self, row: int, checked: bool) -> None:
        if row < 0 or row >= self.rowCount():
            return
        item = self.item(row, PAGE_COL_SELECT)
        if item is None:
            return
        self.blockSignals(True)
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.blockSignals(False)
        ref = self.window_ref
        if ref is None:
            return
        key = item.data(QtCore.Qt.UserRole)
        if key is None:
            id_item = self.item(row, PAGE_COL_ID)
            key = id_item.text() if id_item is not None else ""
        if hasattr(ref, "page_selection_states"):
            ref.page_selection_states[key] = checked
        if hasattr(ref, "_update_page_selection_summary"):
            ref._update_page_selection_summary()

    def _toggle_row_selection(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        self._set_row_selection(row, not self._is_row_selected(row))

    def _on_page_table_cell_clicked(self, row: int, col: int) -> None:
        if col != PAGE_COL_SELECT:
            return
        self._toggle_row_selection(row)

    def _apply_toggle_drag_selection(self, row: int) -> None:
        if self._drag_last_row is None or self._drag_base_state is None:
            return
        start_row = min(self._drag_last_row, row)
        end_row = max(self._drag_last_row, row)
        for target_row in range(start_row, end_row + 1):
            self._set_row_selection(target_row, not self._drag_base_state)
        self._drag_last_row = row


def apply_small_button_style(button: QtWidgets.QPushButton) -> None:
    button.setStyleSheet(
        """
        QPushButton {
            background: #0284c7; color: #ffffff; border: 1px solid #0284c7;
            border-radius: 6px; padding: 2px 8px; min-height: 20px; min-width: 52px;
            font-weight: 700; font-size: 8pt;
        }
        QPushButton:hover { background: #0ea5e9; border-color: #0ea5e9; }
        QPushButton:pressed { background: #0369a1; }
        QPushButton:disabled { background: #e2e8f0; border-color: #cbd5e1; color: #64748b; }
        """
    )


def build_overlay(parent: QtWidgets.QWidget) -> tuple:
    overlay = QtWidgets.QWidget(parent)
    overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
    overlay.setStyleSheet("background: rgba(0,0,0,0.25);")
    overlay.setGeometry(parent.rect())
    overlay_label = QtWidgets.QLabel("", overlay)
    overlay_label.setStyleSheet("color: white; font-weight: 800; background: transparent;")
    overlay_label.setAlignment(QtCore.Qt.AlignCenter)
    overlay_label.setWordWrap(True)
    overlay_label.setGeometry(0, 0, parent.width(), parent.height())
    overlay.hide()
    return overlay, overlay_label


MAIN_STYLESHEET = """
* { font-family: "Nunito"; font-size: 9pt; }
QMainWindow { background: #f8fafc; color: #0f172a; }
QWidget { color: #0f172a; }
QGroupBox {
    background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1;
    border-radius: 8px; padding: 6px; margin-top: 8px; font-weight: 800;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QLabel { color: #334155; font-weight: 650; }
QLineEdit, QPlainTextEdit, QTextEdit, QDateTimeEdit, QTimeEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background: #ffffff; color: #0f172a; selection-background-color: #38bdf8;
    selection-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px;
    padding: 3px 6px; font-weight: 600;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus, QComboBox:focus {
    border: 1px solid #0284c7;
}
QPushButton {
    background: #0284c7; color: #ffffff; border: 1px solid #0284c7;
    border-radius: 6px; padding: 3px 10px; min-height: 22px; min-width: 64px; font-weight: 700; font-size: 8pt;
}
QPushButton:hover { background: #0ea5e9; border-color: #0ea5e9; }
QPushButton:pressed { background: #0369a1; }
QPushButton:disabled { background: #e2e8f0; border-color: #cbd5e1; color: #64748b; }
QTableWidget {
    background: #ffffff; alternate-background-color: #f8fafc; color: #0f172a;
    gridline-color: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 8pt;
}
QTableWidget::item { color: #0f172a; padding: 1px 3px; }
QTableWidget::item:selected { background: #dbeafe; color: #0f172a; }
QHeaderView::section { background: #e2e8f0; color: #0f172a; padding: 3px 5px; border: 1px solid #cbd5e1; font-weight: 800; }
QHeaderView::section:vertical { background: #f8fafc; color: #334155; padding: 2px 4px; border: 1px solid #e2e8f0; }
QTableCornerButton::section { background: #e2e8f0; border: 1px solid #cbd5e1; }
QTableWidget QHeaderView::vertical { background: transparent; }
QCheckBox { color: #334155; font-weight: 700; spacing: 6px; }
QTabWidget::pane { border: 1px solid #cbd5e1; background: #f8fafc; border-radius: 8px; }
QTabBar::tab {
    background: #f1f5f9; color: #334155; padding: 4px 12px; min-height: 22px;
    min-width: 84px; border: 1px solid #cbd5e1; border-radius: 6px;
    margin-right: 4px; margin-bottom: 3px; font-weight: 800; font-size: 8pt; text-align: center;
}
QTabBar::tab:hover { background: #e2e8f0; }
QTabBar::tab:selected { background: #0284c7; color: #ffffff; border-color: #0284c7; }
QStatusBar { background: #f8fafc; color: #0f172a; }
QToolTip { background: #ffffff; color: #0f172a; border: 1px solid #0284c7; padding: 4px; }
"""
