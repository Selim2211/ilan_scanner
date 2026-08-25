"""Upwork OAuth2 token yonetimi: al, sakla, suresi dolunca kendi yenile.

Upwork access token'i ~24 saatte doluyor. Ajan surekli calistigi icin token'in
elle tazelenmesi mumkun degil; bu modul akisi kendi yurutur:

    1. `client_credentials` denenir (kullanici etkilesimi yok).
    2. Reddedilirse yetkilendirme kodu akisi: `authorization_url()` -> tarayici ->
       adres cubugundaki `code` -> `exchange_code()`.
    3. Token `data/upwork_token.json` icinde durur; suresi dolmadan once
       `refresh_token` ile sessizce yenilenir.

.env icinde hazir bir UPWORK_TOKEN varsa o kullanilir (yenilenmez) - eski kurulumlar bozulmasin.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import resolve_path
from .base import FetchError

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
AUTHORIZE_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
DEFAULT_REDIRECT = "https://localhost/callback"
TOKEN_FILE = "data/upwork_token.json"

#: Token bu kadar saniye kala yenilenir (istek ortasinda dolmasin).
REFRESH_MARGIN = 120

#: Token alinamadiktan sonra bu kadar saniye tekrar denenmez.
#: Anahtar Upwork incelemesindeyken ("disabled") her hizli turda token istemek
#: bosuna trafik yaratir; ajan gunlerce calisiyor.
RETRY_COOLDOWN = 1800

_lock = threading.Lock()
_blocked_until = 0.0
_last_error = ""


def _credentials() -> tuple[str, str, str]:
    """(client_id, secret, redirect). Redirect bos olabilir: masaustu tipi uygulamalarda
    Upwork callback URL istemiyor, o zaman istege hic eklenmez."""
    client_id = os.environ.get("UPWORK_CLIENT_ID", "").strip()
    secret = os.environ.get("UPWORK_CLIENT_SECRET", "").strip()
    redirect = os.environ.get("UPWORK_REDIRECT_URI", DEFAULT_REDIRECT).strip()
    return client_id, secret, redirect


def token_path() -> Path:
    return resolve_path(os.environ.get("UPWORK_TOKEN_FILE", "") or TOKEN_FILE)


def load_stored() -> dict[str, Any]:
    path = token_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("upwork token dosyasi okunamadi: %s", path)
        return {}


def save_stored(data: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _post_token(payload: dict[str, str]) -> dict[str, Any]:
    """Token ucuna istek atar; hata mesajini okunur hale getirir."""
    client_id, secret, _ = _credentials()
    payload = dict(payload, client_id=client_id, client_secret=secret)
    try:
        response = httpx.post(TOKEN_URL, data=payload, timeout=30.0,
                              headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise FetchError(f"Upwork token ucuna ulasilamadi: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:200]}
    if response.status_code >= 400 or "access_token" not in body:
        detail = body.get("error_description") or body.get("error") or body
        raise FetchError(f"Upwork token alinamadi (HTTP {response.status_code}): {detail}")
    return body


def _store(body: dict[str, Any], grant: str) -> dict[str, Any]:
    stored = load_stored()
    data = {
        "access_token": body["access_token"],
        # yenileme token'i client_credentials akisinda gelmeyebilir; eskisini koru
        "refresh_token": body.get("refresh_token") or stored.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 86400)),
        "grant": grant,
    }
    save_stored(data)
    return data


def authorization_url(state: str = "sap-radar") -> str:
    """Tarayicida acilacak yetkilendirme adresi."""
    client_id, _, redirect = _credentials()
    from urllib.parse import urlencode

    params = {"response_type": "code", "client_id": client_id, "state": state}
    if redirect:
        params["redirect_uri"] = redirect
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict[str, Any]:
    """Yetkilendirme kodunu access + refresh token'a cevirir."""
    _, _, redirect = _credentials()
    payload = {"grant_type": "authorization_code", "code": code.strip()}
    if redirect:
        payload["redirect_uri"] = redirect
    body = _post_token(payload)
    log.info("upwork: yetkilendirme kodu token'a cevrildi")
    return _store(body, "authorization_code")


def client_credentials() -> dict[str, Any]:
    """Kullanici etkilesimi olmayan akis. Uygulama desteklemiyorsa FetchError."""
    body = _post_token({"grant_type": "client_credentials"})
    log.info("upwork: client_credentials token alindi")
    return _store(body, "client_credentials")


def refresh(stored: dict[str, Any] | None = None) -> dict[str, Any]:
    stored = stored or load_stored()
    if stored.get("grant") == "client_credentials" or not stored.get("refresh_token"):
        # yenileme token'i yoksa yeni bir client_credentials token'i almak esdegerdir
        return client_credentials()
    body = _post_token({"grant_type": "refresh_token",
                        "refresh_token": stored["refresh_token"]})
    log.info("upwork: token yenilendi")
    return _store(body, stored.get("grant", "authorization_code"))


def token_status() -> dict[str, Any]:
    """CLI'nin gosterdigi ozet: token var mi, kac saniye omru kaldi."""
    static = os.environ.get("UPWORK_TOKEN", "").strip()
    if static:
        return {"source": "env", "grant": "static", "seconds_left": None, "has_refresh": False}
    stored = load_stored()
    if not stored:
        return {"source": "yok", "grant": "", "seconds_left": None, "has_refresh": False}
    return {
        "source": str(token_path()),
        "grant": stored.get("grant", ""),
        "seconds_left": int(stored.get("expires_at", 0) - time.time()),
        "has_refresh": bool(stored.get("refresh_token")),
    }


def reset_backoff() -> None:
    """Bekleme suresini sifirlar; elle 'upwork-auth' calistirinca hemen denensin."""
    global _blocked_until, _last_error
    _blocked_until, _last_error = 0.0, ""


def get_token(force_refresh: bool = False) -> str:
    """Gecerli access token. Gerekirse alir ya da yeniler.

    Tek kilit altinda calisir: hizli tur ile tam tur ayni anda token yenilemesin.
    Basarisiz denemeden sonra RETRY_COOLDOWN boyunca tekrar denenmez.
    """
    global _blocked_until, _last_error
    static = os.environ.get("UPWORK_TOKEN", "").strip()
    if static and not force_refresh:
        return static

    client_id, secret, _ = _credentials()
    if not client_id or not secret:
        raise FetchError(
            "Upwork kimlik bilgisi yok. .env icine UPWORK_CLIENT_ID ve UPWORK_CLIENT_SECRET "
            "ekleyip 'py -m scanner upwork-auth' calistirin."
        )

    with _lock:
        stored = load_stored()
        expired = float(stored.get("expires_at", 0)) - REFRESH_MARGIN <= time.time()
        if stored.get("access_token") and not expired and not force_refresh:
            return str(stored["access_token"])

        if time.time() < _blocked_until:
            kalan = int(_blocked_until - time.time()) // 60
            raise FetchError(f"Upwork token alinamiyor ({_last_error}); {kalan} dk sonra tekrar denenecek")

        try:
            if stored:
                return str(refresh(stored)["access_token"])
            # hic token yok: once etkilesimsiz akisi dene
            return str(client_credentials()["access_token"])
        except FetchError as exc:
            _blocked_until = time.time() + RETRY_COOLDOWN
            _last_error = str(exc)
            raise
