"""Surum bilgisi. Tek kaynak: tepsi "Hakkinda" penceresi ve paket bunu okur."""
from __future__ import annotations

APP_NAME = "İlan Tarayıcı"
VERSION = "1.8.0"
RELEASE_DATE = "2026-09-02"

#: Hakkinda penceresinde madde madde gosterilir - "bu surumde ne degisti".
CHANGELOG = [
    ("1.8.0", "2026-09-02", [
        "Ayarlar > Yapay zeka'ya \"Maliyet\" ekranı eklendi: her modelin harcadığı "
        "token ve tahmini TL tutarı günlük (son 24 saat), haftalık (son 7 gün) ve "
        "aylık (son 30 gün) olarak tabloda görünüyor; ekranın başında şu an hangi "
        "modelin kullanıldığı ve o modelin 1M token fiyatı yazıyor.",
        "Her başarılı özet çağrısının token sayısı kaydediliyor; tutar modelin "
        "Gemini API fiyatından ve kur ayarındaki USD/TRY oranından hesaplanıyor.",
    ]),
    ("1.7.0", "2026-09-02", [
        "Kapanmış ilanlar artık listeye geri gelmiyor. Asıl sorun buydu: ilan "
        "kapatılıyordu ama kaynak (Jooble) kapanmış ilanı günlerce listesinde "
        "tuttuğu için her taramada geri açılıyordu — ölçüldü, 199 ilan "
        "\"kapandı\" işareti almasına rağmen listede duruyordu.",
        "\"Tümünü kontrol et\" artık ekranda gördüğünüz ilanları kontrol ediyor: "
        "onay penceresindeki sayı ile listedeki sayı aynı.",
        "Kontrol turu belirgin hızlandı: linki hiçbir zaman açılmayan kaynaklara "
        "(Jooble) boşuna istek atılmıyor, doğrudan kaynağın API'sine soruluyor. "
        "\"Doğrulanamadı\" sayısı da artık gerçeği gösteriyor.",
        "API anahtarları veritabanında şifreli saklanıyor; ekranda yalnızca "
        "yıldız görünüyor, hiçbir karakter sızmıyor.",
        "İlan özeti artık kartın üstüne gelince değil, karttaki mor \"i\" "
        "düğmesine tıklayınca açılıyor; pencere ekranın ortasında beliriyor ve "
        "arka planı kilitlemiyor.",
        "Ülke filtresi baştan yazıldı: aranabilir, kıtaya göre gruplu, ilan "
        "sayılı çoklu seçim. Artık gerçekten ülkeye göre filtreliyor.",
        "Izgara görünümünde kartlardaki yarım kesilen satır düzeltildi.",
    ]),
    ("1.6.0", "2026-08-27", [
        "İlan özetleri artık yapay zeka ile hazırlanıyor: kısa özet ve o ilana "
        "başvurmak için gereken \"olmazsa olmaz\" koşullar altı çizili listeleniyor.",
        "Yabancı dildeki ilan doğrudan İngilizce özetleniyor.",
        "Kartın üstüne gelince imlecin yanında bir halka dolar; pencere ancak "
        "halka dolduktan sonra açılır — listede gezinirken yanlışlıkla açılmaz.",
        "Ana ekrandaki sayılar düzeltildi: her karo, tıklayınca açılan listeyle "
        "birebir aynı sayıyı gösteriyor (önce fazla sayıyorlardı; 'son 24 saat' "
        "karosu tıklanınca bütün listeyi açıyordu).",
        "Ayarlar > Yapay zeka: motoru açıp kapatma, model seçimi, API anahtarı ve "
        "minimum skor eşiği (eşiğin altındaki ilan için token harcanmaz).",
        "Her özet bir kez üretilip saklanıyor; aynı ilan ikinci kez işlenmiyor.",
        "Yabancı dildeki ilanlarda 'olmazsa olmaz' maddeleri de artık İngilizceye "
        "çevriliyor (önce özet İngilizce gelirken maddeler ilanın dilinde kalıyordu).",
        "'Tümünü kontrol et' kapanmış ilanları çok daha iyi yakalıyor: tur başına "
        "API ile sorulan ilan sayısı arttı ve uzun süredir taramada görünmeyen bir "
        "ilan API'de de bulunamıyorsa ikinci tur beklenmeden kapatılıyor.",
        "Kontrol turunun ilerleme çubuğu API aşamasında da akıyor ve Durdur "
        "düğmesi o sırada anında işliyor (önce dakikalarca donmuş görünüyordu).",
    ]),
    ("1.5.0", "2026-08-26", [
        "İlan kartının üstüne gelince ekranın ortasında özet penceresi açılıyor: "
        "açıklama ve ilanın listeye girmesini sağlayan zorunlu kelimeler.",
        "Almanca / Hollandaca / Fransızca ilanların açıklaması kutuda "
        "İngilizce gösteriliyor (ilk bakışta çevrilir, sonra saklanır).",
        "Yabancı dildeki ilanlar artık doğru puanlanıyor: 'freiberuflich', "
        "'Werkvertrag', 'Fernarbeit' gibi terimler proje bazlı/uzaktan olarak "
        "tanınıyor — önceden bu ilanlar sözleşmeli sayılmıyordu.",
        "'Tümünü kontrol et': kontrol artık ekrandaki filtreye takılmıyor, "
        "bütün aktif ilanları tarıyor ve en uzun süredir bakılmayandan başlıyor.",
    ]),
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
