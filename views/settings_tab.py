from typing import Any, Callable, Dict, Optional

from PyQt5 import QtCore, QtWidgets

from views.widgets import apply_small_button_style


class SettingsTab(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._on_proxy_changed: Optional[Callable] = None
        self._on_concurrency_changed: Optional[Callable] = None
        self._build_ui()

    def set_callbacks(self, on_proxy_changed: Callable, on_concurrency_changed: Callable) -> None:
        self._on_proxy_changed = on_proxy_changed
        self._on_concurrency_changed = on_concurrency_changed

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        utility_group = QtWidgets.QGroupBox("Tiện ích")
        utility_layout = QtWidgets.QVBoxLayout(utility_group)

        desc = QtWidgets.QLabel("Các thao tác kiểm tra, kích hoạt và dọn cache được gom vào đây.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        utility_layout.addWidget(desc)

        actions = QtWidgets.QHBoxLayout()
        self.check_tokens_btn = QtWidgets.QPushButton("Kiểm tra token ngay")
        apply_small_button_style(self.check_tokens_btn)
        actions.addWidget(self.check_tokens_btn)

        self.activate_trial_btn = QtWidgets.QPushButton("Kích hoạt mã")
        apply_small_button_style(self.activate_trial_btn)
        actions.addWidget(self.activate_trial_btn)

        self.refresh_token_btn = QtWidgets.QPushButton("Refresh token")
        apply_small_button_style(self.refresh_token_btn)
        actions.addWidget(self.refresh_token_btn)

        self.clear_cache_btn = QtWidgets.QPushButton("Xóa cache")
        apply_small_button_style(self.clear_cache_btn)
        actions.addWidget(self.clear_cache_btn)

        self.reset_license_btn = QtWidgets.QPushButton("Xóa license & nhập lại")
        apply_small_button_style(self.reset_license_btn)
        actions.addWidget(self.reset_license_btn)

        utility_layout.addLayout(actions)
        layout.addWidget(utility_group)

        proxy_group = QtWidgets.QGroupBox("Proxy")
        proxy_layout = QtWidgets.QVBoxLayout(proxy_group)

        self.proxy_enabled_checkbox = QtWidgets.QCheckBox("Dùng proxy")
        self.proxy_enabled_checkbox.toggled.connect(self._proxy_changed)
        proxy_layout.addWidget(self.proxy_enabled_checkbox)

        provider_row = QtWidgets.QHBoxLayout()
        provider_row.addWidget(QtWidgets.QLabel("Nhà cung cấp:"))
        self.proxy_provider_combo = QtWidgets.QComboBox()
        self.proxy_provider_combo.addItems(["kiotproxy", "generic"])
        self.proxy_provider_combo.currentTextChanged.connect(self._proxy_changed)
        provider_row.addWidget(self.proxy_provider_combo)
        proxy_layout.addLayout(provider_row)

        key_row = QtWidgets.QHBoxLayout()
        key_row.addWidget(QtWidgets.QLabel("API key:"))
        self.proxy_key_input = QtWidgets.QLineEdit()
        self.proxy_key_input.setPlaceholderText("Nhập API key hoặc token")
        self.proxy_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.proxy_key_input.textChanged.connect(self._proxy_changed)
        key_row.addWidget(self.proxy_key_input)
        proxy_layout.addLayout(key_row)

        self.proxy_endpoint_input = QtWidgets.QLineEdit()
        self.proxy_endpoint_input.setPlaceholderText("Ví dụ: https://api.zingproxy.com/open/change-ip/{key}")
        self.proxy_endpoint_input.textChanged.connect(self._proxy_changed)
        proxy_layout.addWidget(QtWidgets.QLabel("Mẫu endpoint:"))
        proxy_layout.addWidget(self.proxy_endpoint_input)

        layout.addWidget(proxy_group)

        concurrency_group = QtWidgets.QGroupBox("Đăng đồng thời")
        concurrency_layout = QtWidgets.QVBoxLayout(concurrency_group)

        conc_desc = QtWidgets.QLabel("Bật chế độ đăng đồng thời để xử lý nhiều page cùng lúc.")
        conc_desc.setWordWrap(True)
        conc_desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        concurrency_layout.addWidget(conc_desc)

        self.concurrent_enabled_checkbox = QtWidgets.QCheckBox("Bật đăng đồng thời")
        self.concurrent_enabled_checkbox.toggled.connect(self._concurrency_changed)
        concurrency_layout.addWidget(self.concurrent_enabled_checkbox)

        controls = QtWidgets.QGridLayout()
        controls.setSpacing(10)

        threads_label = QtWidgets.QLabel("Số luồng:")
        threads_label.setStyleSheet("font-weight: 600;")
        controls.addWidget(threads_label, 0, 0)
        self.concurrent_threads_spin = QtWidgets.QSpinBox()
        self.concurrent_threads_spin.setRange(1, 10)
        self.concurrent_threads_spin.setValue(2)
        self.concurrent_threads_spin.valueChanged.connect(self._concurrency_changed)
        controls.addWidget(self.concurrent_threads_spin, 0, 1)

        delay_label = QtWidgets.QLabel("Độ trễ (giây):")
        delay_label.setStyleSheet("font-weight: 600;")
        controls.addWidget(delay_label, 1, 0)
        self.concurrent_delay_spin = QtWidgets.QDoubleSpinBox()
        self.concurrent_delay_spin.setRange(0.0, 300.0)
        self.concurrent_delay_spin.setSingleStep(0.5)
        self.concurrent_delay_spin.setValue(2.0)
        self.concurrent_delay_spin.valueChanged.connect(self._concurrency_changed)
        controls.addWidget(self.concurrent_delay_spin, 1, 1)

        concurrency_layout.addLayout(controls)
        hint = QtWidgets.QLabel("Mẹo: dùng 2-4 luồng nếu mạng ổn định.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #0284c7; font-size: 11px; font-weight: 700;")
        concurrency_layout.addWidget(hint)

        layout.addWidget(concurrency_group)
        layout.addStretch(1)

    def _proxy_changed(self) -> None:
        if self._on_proxy_changed:
            self._on_proxy_changed()

    def _concurrency_changed(self) -> None:
        if self._on_concurrency_changed:
            self._on_concurrency_changed()

    def get_proxy_config(self) -> Dict[str, Any]:
        return {
            "enabled": self.proxy_enabled_checkbox.isChecked(),
            "key": self.proxy_key_input.text().strip(),
            "provider": self.proxy_provider_combo.currentText().strip() or "kiotproxy",
            "endpoint_template": self.proxy_endpoint_input.text().strip(),
        }

    def get_concurrency_config(self) -> Dict[str, Any]:
        return {
            "enabled": self.concurrent_enabled_checkbox.isChecked(),
            "threads": max(1, self.concurrent_threads_spin.value()),
            "delay": float(self.concurrent_delay_spin.value()),
        }

    def load_proxy_config(self, data: Dict[str, Any]) -> None:
        self.proxy_enabled_checkbox.setChecked(bool(data.get("enabled", False)))
        self.proxy_key_input.setText(str(data.get("key", "")))
        provider = str(data.get("provider", "kiotproxy") or "kiotproxy")
        idx = self.proxy_provider_combo.findText(provider)
        if idx >= 0:
            self.proxy_provider_combo.setCurrentIndex(idx)
        self.proxy_endpoint_input.setText(str(data.get("endpoint_template", "")))

    def load_concurrency_config(self, data: Dict[str, Any]) -> None:
        self.concurrent_enabled_checkbox.setChecked(bool(data.get("enabled", False)))
        self.concurrent_threads_spin.setValue(int(data.get("threads", 2)))
        self.concurrent_delay_spin.setValue(float(data.get("delay", 2.0)))
