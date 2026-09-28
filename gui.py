import os
import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets


def _qt_message_handler(mode, context, message):
    if "Cannot queue arguments of type 'QVector<int>'" in message:
        return


if hasattr(QtCore, "qInstallMessageHandler"):
    QtCore.qInstallMessageHandler(_qt_message_handler)


def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _ensure_runtime_files(runtime_dir: Path) -> None:
    import shutil
    source_dir = Path(getattr(sys, "_MEIPASS", runtime_dir)).resolve()
    for relative_path in ("facebook_config.json", "resources/logo.svg", "resources/icon.png", "resources/icon.ico"):
        source = source_dir / relative_path
        target = runtime_dir / relative_path
        if target.exists() or not source.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except Exception:
            pass


def _setup_ui_logger() -> None:
    import logging
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(_runtime_dir() / "facebook_scraper.log", encoding="utf-8", mode="a"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    _setup_ui_logger()

    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Nunito", 9))

    project_dir = _runtime_dir()
    _ensure_runtime_files(project_dir)

    from di.container import Container
    container = Container(project_dir)

    from views.main_window import FacebookPageManagerWindow
    window = FacebookPageManagerWindow(container)

    from presenters.main_presenter import MainPresenter
    presenter = MainPresenter(container, window)
    window.set_presenter(presenter)

    presenter.on_app_start()

    QtCore.QTimer.singleShot(300, presenter.on_trial_check)

    trial_timer = QtCore.QTimer()
    trial_timer.timeout.connect(presenter._update_trial_status)
    trial_timer.start(1000)

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    res_dir = _runtime_dir() / "resources"
    if not res_dir.exists():
        try:
            res_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    main()
