"""Kullanici girisi, oturumlar ve yetkiler.

Ayri bir dosya degil, ayni SQLite veritabani kullanilir (users / sessions
tablolari). Sifreler pbkdf2-sha256 ile saklanir; duz sifre hicbir yerde tutulmaz.

Yetki modeli sade: admin her seyi yapar, digerlerinin izinleri
`users.permissions` icinde JSON olarak durur. `PERMISSIONS` bu dosyadaki tek
dogru kaynak - panel yetki listesini buradan uretir, yeni yetki eklemek icin
sadece buraya satir yazmak yeter.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Cerezde tasinan oturum anahtari
COOKIE_NAME = "radar_oturum"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    is_admin      INTEGER DEFAULT 0,
    is_active     INTEGER DEFAULT 1,
    permissions   TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_se_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_se_exp ON sessions(expires_at);
"""

#: (anahtar, baslik, aciklama) - Ayarlar > Admin ekranindaki anahtarlar bunlardan uretilir
PERMISSION_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("İlanlar", [
        ("view_list", "İlan listesini gör",
         "Kapalıysa kullanıcı hiçbir ilan göremez."),
        ("mark_status", "Takip / başvuru işaretle",
         "İlanı takibe alma ve 'başvurdum' işaretleme."),
        ("report_closed", "'Artık aktif değil' bildir",
         "İlanı elle kapatır."),
        ("flag_projects", "Sorunlu ilan işaretle",
         "Bölge kısıtlı / açılmayan ilanları işaretleme ve işareti kaldırma."),
        ("sweep_links", "Listeyi canlı kontrol et",
         "Filtredeki ilanların linklerini açıp kapananları kapatır. Ağ trafiği üretir."),
        ("export", "Excel dışa aktar",
         "Listeyi dosyaya aktarma."),
    ]),
    ("Bildirimler", [
        ("view_notifications", "Bildirimleri gör", "Bildirim sekmesine erişim."),
        ("delete_notifications", "Bildirim sil", "Tek tek veya toplu silme."),
    ]),
    ("Tarama", [
        ("trigger_scan", "Elle tarama başlat", "'Şimdi tara' düğmesi."),
        ("edit_scan", "Tarama ayarlarını değiştir", "Tur aralıkları, sayfa boyutu."),
    ]),
    ("Ayarlar", [
        ("view_settings", "Ayarlar sayfasını gör",
         "Kapalıysa Ayarlar sekmesi hiç görünmez."),
        ("edit_search", "Ne arıyoruz — kelime ve sorgular", "Puanlama sözlüğünü değiştirme."),
        ("edit_sources", "Kaynakları yönet", "Kaynak açma/kapama, yeni kaynak ekleme."),
        ("edit_keys", "API anahtarlarını yönet", "Anahtar girme ve silme."),
        ("edit_ai", "Yapay zeka ayarları",
         "İlan özeti motoru: model, minimum skor eşiği ve Gemini anahtarı."),
        ("manage_profiles", "Arama profillerini yönet", "Profil ekleme, silme, düzenleme."),
        ("maintenance", "Bakım işlemleri", "Geçici dosya ve log temizliği."),
    ]),
    ("Yönetim", [
        ("manage_users", "Kullanıcıları yönet",
         "Kullanıcı açma, şifre değiştirme, yetki verme. Dikkatli dağıtın."),
    ]),
]

#: Duz liste - dogrulama ve varsayilanlar icin
PERMISSIONS = [key for _, group in PERMISSION_GROUPS for key, _, _ in group]

#: Yeni acilan kullanicinin varsayilan yetkileri: okuyabilir, isaretleyebilir
DEFAULT_PERMISSIONS = ["view_list", "view_notifications", "mark_status", "export"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """(hash, salt) doner. Salt verilmezse yeni uretilir."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), PBKDF2_ROUNDS)
    return digest.hex(), salt


def check_password(password: str, stored_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate, stored_hash)


class User:
    """Oturum acmis kullanici; sablonlar ve rota kontrolleri bunu kullanir."""

    def __init__(self, row: sqlite3.Row):
        self.id = row["id"]
        self.username = row["username"]
        self.full_name = row["full_name"] or ""
        self.is_admin = bool(row["is_admin"])
        self.is_active = bool(row["is_active"])
        self.last_login_at = row["last_login_at"]
        try:
            granted = json.loads(row["permissions"] or "{}")
        except json.JSONDecodeError:
            granted = {}
        self.permissions = {k: bool(v) for k, v in granted.items()}

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    @property
    def initials(self) -> str:
        """Sag ustteki yuvarlagin icindeki harfler."""
        parts = [p for p in self.display_name.replace(".", " ").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def can(self, permission: str) -> bool:
        """Admin her seyi yapar; digerleri acikca verilen yetkiyi."""
        return self.is_admin or self.permissions.get(permission, False)

    def as_dict(self) -> dict:
        return {"id": self.id, "username": self.username, "full_name": self.full_name,
                "is_admin": self.is_admin, "permissions": self.permissions}


class Auth:
    """users / sessions tablolari. Storage gibi acilip kapatilir."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- kurulum ---------------------------------------------------------
    def ensure_admin(self, username: str = "admin", password: str = "123456") -> bool:
        """Hic kullanici yoksa ilk yoneticiyi acar. True = olusturuldu."""
        count = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return False
        self.create_user(username, password, is_admin=True, full_name="Yönetici")
        return True

    # --- kullanicilar ----------------------------------------------------
    def create_user(self, username: str, password: str, is_admin: bool = False,
                    full_name: str = "", permissions: list[str] | None = None) -> int:
        digest, salt = hash_password(password)
        granted = {k: True for k in (permissions if permissions is not None
                                     else DEFAULT_PERMISSIONS) if k in PERMISSIONS}
        cur = self.conn.execute(
            "INSERT INTO users (username, full_name, password_hash, salt, is_admin, "
            "permissions, created_at) VALUES (?,?,?,?,?,?,?)",
            (username.strip(), full_name.strip(), digest, salt, int(is_admin),
             json.dumps(granted), _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def users(self) -> list[User]:
        return [User(r) for r in self.conn.execute(
            "SELECT * FROM users ORDER BY is_admin DESC, username COLLATE NOCASE")]

    def get_user(self, user_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(row) if row else None

    def by_username(self, username: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()

    def admin_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()[0])

    def set_permissions(self, user_id: int, granted: list[str]) -> None:
        payload = {k: True for k in granted if k in PERMISSIONS}
        self.conn.execute("UPDATE users SET permissions = ? WHERE id = ?",
                          (json.dumps(payload), user_id))
        self.conn.commit()

    def set_profile(self, user_id: int, full_name: str, is_admin: bool, is_active: bool) -> None:
        self.conn.execute(
            "UPDATE users SET full_name = ?, is_admin = ?, is_active = ? WHERE id = ?",
            (full_name.strip(), int(is_admin), int(is_active), user_id))
        self.conn.commit()

    def set_password(self, user_id: int, password: str) -> None:
        digest, salt = hash_password(password)
        self.conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                          (digest, salt, user_id))
        # sifre degisince o kullanicinin acik oturumlari dusurulur
        self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def delete_user(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    # --- oturum ----------------------------------------------------------
    def login(self, username: str, password: str) -> tuple[str, User] | None:
        row = self.by_username(username)
        if row is None or not row["is_active"]:
            # kullanici yoksa da ayni sureyi harcayalim: varlik sizdirmasin
            hash_password(password, secrets.token_hex(16))
            return None
        if not check_password(password, row["password_hash"], row["salt"]):
            return None
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        self.conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, row["id"], _now(), expires))
        self.conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        self.conn.commit()
        return token, User(self.by_username(username))

    def session_user(self, token: str | None) -> User | None:
        if not token:
            return None
        row = self.conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > ? AND u.is_active = 1",
            (token, _now())).fetchone()
        return User(row) if row else None

    def logout(self, token: str | None) -> None:
        if token:
            self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self.conn.commit()

    def expired_session_count(self) -> int:
        """Bakim ekrani: SILINECEK oturum sayisi (purge_sessions ile ayni olcut)."""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE expires_at <= ?", (_now(),)).fetchone()[0])

    def purge_sessions(self) -> int:
        """Suresi dolmus oturumlari siler (bakim ekrani cagirir)."""
        cur = self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
        self.conn.commit()
        return cur.rowcount


def validate_username(name: str) -> str:
    """Bos dizge = gecerli. Aksi halde hata metni doner."""
    name = name.strip()
    if len(name) < 3:
        return "Kullanıcı adı en az 3 karakter olmalı."
    if len(name) > 32:
        return "Kullanıcı adı en fazla 32 karakter olabilir."
    if not all(c.isalnum() or c in "._-" for c in name):
        return "Kullanıcı adında yalnızca harf, rakam, nokta, alt çizgi ve tire olabilir."
    return ""


def validate_password(password: str) -> str:
    if len(password) < 6:
        return "Şifre en az 6 karakter olmalı."
    if len(password) > 128:
        return "Şifre çok uzun."
    return ""
