# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


block_cipher = None
project_dir = Path.cwd()

datas = []
for source, target in (
    ("package_defaults/facebook_config.json", "."),
    ("selected_pages_config.xlsx", "."),
    ("excel_config_template.xlsx", "."),
    ("resources", "resources"),
):
    if (project_dir / source).exists():
        datas.append((source, target))


a = Analysis(
    ["gui.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "openpyxl",
        "dotenv",
        "requests",
        "models",
        "models.database",
        "models.config_model",
        "models.page_detail_model",
        "di",
        "di.interfaces",
        "di.container",
        "services",
        "services.page_service",
        "services.post_service",
        "services.config_service",
        "services.license_service",
        "services.page_detail_service",
        "services.stats_service",
        "presenters",
        "presenters.main_presenter",
        "presenters.page_list_presenter",
        "presenters.config_presenter",
        "presenters.settings_presenter",
        "presenters.page_detail_presenter",
        "views",
        "views.main_window",
        "views.quick_config_tab",
        "views.recent_posts_tab",
        "views.log_tab",
        "views.settings_tab",
        "views.page_detail_dialog",
        "views.video_selection_dialog",
        "views.stats_tab",
        "views.license_dialog",
        "views.widgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FBPageManager_TMV",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="resources/icon.ico",
    version="version_info.txt",
)
