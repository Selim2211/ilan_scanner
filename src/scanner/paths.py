"""Dosya yollari: gelistirme klasoru mu, kurulu program mi?

Kurulu exe `C:\\Program Files\\...` altinda calisir ve oraya yazamaz. Bu yuzden
iki yol ayrilir:

    bundle_dir()  okunacak dosyalar  -> config/*.yaml, sablonlar, statik dosyalar
    data_dir()    yazilacak dosyalar -> veritabani, loglar, token, Excel ciktilari

Gelistirme makinesinde ikisi de proje klasorudur; davranis degismez.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "SAP Proje Radari"


def is_frozen() -> bool:
    """PyInstaller ile paketlenmis exe icinde miyiz?"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Salt okunur kaynaklarin koku."""
    if is_frozen():
        # onefile modunda gecici cikarma klasoru, onedir modunda exe klasoru
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Yazilabilir veri klasoru. Gelistirmede proje kokü, kuruluda %LOCALAPPDATA%."""
    override = os.environ.get("RADAR_DATA_DIR", "").strip()
    if override:
        path = Path(override)
    elif is_frozen():
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        path = Path(base) / APP_NAME
    else:
        path = bundle_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource(relative: str) -> Path:
    """Paketten okunacak dosya (config/config.yaml gibi)."""
    return bundle_dir() / relative


def user_file(relative: str) -> Path:
    """Yazilacak dosya; ust klasoru olusturulur."""
    path = data_dir() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = data_dir() / "output" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
