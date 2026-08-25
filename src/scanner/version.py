"""Surum bilgisi. Tek kaynak: tepsi "Hakkinda" penceresi ve paket bunu okur."""
from __future__ import annotations

APP_NAME = "İlan Tarayıcı"
VERSION = "1.4.0"
RELEASE_DATE = "2026-08-23"

#: Hakkinda penceresinde madde madde gosterilir - "bu surumde ne degisti".
CHANGELOG = [
    ("1.4.0", "2026-08-23", [
        "Çok profilli tarama: tam turda bütün arama profilleri besleniyor — "
        "artık başka profildeyken diğerinin listesi bayatlamıyor.",
        "Profil başına 'arka planda taransın' anahtarı.",
        "'Neden bu puan?' dökümü: her ilanın hangi kelimeden kaç puan aldığı görünüyor.",
        "Aynı ilanın başka kaynaklarda da bulunduğu gösteriliyor.",
        "'Kontrol ediliyor' rozeti artık gerçek eşiğe göre basılıyor.",
        "Uzun süredir kayıp ilanlar son çare olarak kapatılıyor; doğrulama en çok "
        "kayıp olandan başlıyor.",
        "Boş bırakılan sinyal ayarı artık uzaktan/proje bazlı önceliğini silmiyor.",
        "Bakım ekranındaki sayılar gerçekten silinecek kadarı gösteriyor.",
    ]),
    ("1.3.0", "2026-08-21", [
        "Koyu / açık tema anahtarı — seçim tarayıcıda saklanır.",
        "Her arama profilinin kendi bildirimleri ve kendi takip listesi var.",
        "Yönetici, kullanıcılara arama profili atayabiliyor; kullanıcı yalnızca "
        "kendisine atanan profiller arasında geçiş yapıyor.",
        "Profil geçişi düzeltildi: boş bırakılan alanlar artık önceki profilden sızmıyor.",
        "Kullanıcı yönetimi ayrı bir Admin paneline taşındı.",
        "Ayarlar bölümleri renklerle ayrıldı.",
    ]),
    ("1.2.0", "2026-08-21", [
        "Kullanıcı girişi ve yetki yönetimi eklendi (admin paneli).",
        "Arama profilleri: farklı parametre setleri arasında tek tıkla geçiş.",
        "Gizlenen ilanlar listesi — tek tek veya toplu silinebiliyor.",
        "Sorunlu ilan işareti: bölge kısıtlı / açılmayan ilanlar sarı halka ile ayrılıyor.",
        "Ayarlar > Bakım: geçici dosya, log ve eski çıktı temizliği.",
        "Filtre ekranı sadeleştirildi.",
        "Tepsi menüsü: Başlat / Durdur / Paneli Aç / Hakkında.",
    ]),
    ("1.1.0", "2026-08-19", [
        "Masaüstü penceresi ve tepsi ikonu.",
        "Bütçeye göre sıralama, tarih aralığı filtresi.",
    ]),
    ("1.0.0", "2026-08-17", [
        "İlk sürüm: çoklu kaynak taraması, puanlama, web paneli.",
    ]),
]


def about_lines() -> list[tuple[str, str]]:
    """Hakkinda penceresindeki (etiket, deger) satirlari."""
    import platform
    import sys

    from . import paths

    return [
        ("Sürüm", f"{VERSION}  ({RELEASE_DATE})"),
        ("Python", platform.python_version()),
        ("Çalışma şekli", "kurulu paket" if paths.is_frozen() else "kaynak koddan"),
        ("Veri klasörü", str(paths.data_dir())),
        ("Program klasörü", str(paths.bundle_dir())),
        ("İşletim sistemi", f"{platform.system()} {platform.release()}"),
        ("Yorumlayıcı", sys.executable),
    ]
