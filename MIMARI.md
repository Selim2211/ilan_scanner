# İlan Tarayıcı — Mimari Dokümantasyonu

Sürüm tarihi: 2026-08-21 · Kod: ~5.900 satır Python · Test: 144 (ağa çıkmaz)

---

## 1. Ne yapar

Program, internetteki iş ve proje panolarını düzenli aralıklarla tarar; aranan konuya uyan
ilanları toplar, tekilleştirir, puanlar ve yerel bir web panelinde listeler. Kapanan ilanları
tespit edip listeden düşürür, yeni ilan geldiğinde bildirim üretir.

Bugünkü arama konusu SAP/ABAP projeleridir, ancak **konu koda gömülü değildir**: aranan
kelimeler, sorgular ve puanlama ağırlıkları panelin Ayarlar ekranından değiştirilir.

Üç temel tasarım kararı, sistemin tamamını şekillendirir:

1. **Kaynak izolasyonu.** Bir kaynak hata verirse tarama durmaz; hata kaydedilir, diğer
   kaynaklar çalışmaya devam eder.
2. **Yokluk ≠ kapanma.** Bir ilanın kaynak listesinde görünmemesi tek başına onu kapatmaz.
3. **Kod açmadan yönetim.** Kaynak ekleme dahil her ayar panelden yapılır; kod ve YAML
   dosyaları son kullanıcının işi değildir.

---

## 2. Katmanlar

Veri tek yönde akar. Her katman yalnızca bir alttakini tanır:

```
  KAYNAKLAR          sources/*.py        dış dünyadan ham ilan çeker
      |
      v
  NORMALİZASYON      normalize.py        metin/tarih/çalışma şekli/bütçe ayrıştırma
      |
      v
  TEKİLLEŞTİRME      dedupe.py           aynı ilan iki kaynakta → tek kayıt
      |
      v
  PUANLAMA           scoring.py          kelime ağırlıkları + çalışma şekli sinyalleri
      |
      v
  DEPOLAMA           storage.py          SQLite: projects / runs / notifications
      |
      v
  SUNUM              web/, export.py, mailer.py, desktop/
```

`pipeline.py` bu zinciri baştan sona yürüten tek modüldür. `scheduler.py` onu zamanlar.

### Modül sorumlulukları

| Modül | Satır | Sorumluluk |
|---|---|---|
| `pipeline.py` | 528 | Tarama akışı: kaynaklar → dedupe → puanlama → DB → kapanan tespiti → bildirim |
| `storage.py` | 608 | SQLite şeması, migration, sorgu/filtre/sıralama, sayfalama |
| `web/app.py` | 593 | FastAPI panel: liste, filtreler, bildirimler, Ayarlar, canlı durum |
| `desktop/launcher.py` | 450 | Tkinter penceresi + tepsi ikonu (terminal bilmeyen kullanıcı için) |
| `cli.py` | 407 | Komut satırı: scan / export / mail / verify / sweep / rescore / serve / watch / stats |
| `normalize.py` | 291 | HTML temizleme, Türkçe fold, tarih, remote/hibrit, sözleşme, bütçe |
| `sources/custom.py` | 274 | Panelden eklenen RSS/JSON kaynakları |
| `scheduler.py` | 267 | Arka plan tarama döngüsü (iki tempo) |
| `sweep.py` | 286 | Temizlik turu: filtredeki ilanların linkini paralel kontrol eder |
| `config.py` | 224 | YAML yükleme + overlay birleştirme + `.env` yönetimi |
| `settings.py` | 197 | Kaynak katalogu, anahtar maskeleme, form ayrıştırma |
| `sources/base.py` | 168 | Kaynak arayüzü + HTTP istemcisi (hız sınırı, retry, hata sınıfları) |
| `scoring.py` | 134 | Puan hesaplama ve eleme |
| `probe.py` | 131 | Tek kaynağı canlı deneme ("veriyi kontrol et") |
| `paths.py` | 63 | Okunacak/yazılacak klasör ayrımı |

---

## 3. Kaynak katmanı

### Arayüz

Her kaynak `BaseSource` sınıfını miras alır ve tek bir metodu doldurur:

```python
class BaseSource:
    name: str                        # kayıtlardaki kısa ad
    title: str                       # panelde görünen ad
    env_vars: tuple[EnvVar, ...]     # gerektirdiği API anahtarları
    list_options: tuple[str, ...]    # panelden düzenlenebilir liste alanları
    probe_trim: tuple[str, ...]      # kontrol sırasında kısaltılacak listeler

    def fetch(self, query: str) -> list[Project]: ...
```

`safe_fetch()` `fetch()`'i sarar ve `(ilanlar, hata_mesajı)` döndürür — hiçbir zaman istisna
fırlatmaz. Kaynak izolasyonu buradan gelir.

### HTTP istemcisi

`HttpClient` httpx üzerine ince bir sarmalayıcıdır: istekler arası bekleme (hız sınırı),
`2**n` geri çekilmeli tekrar deneme, ve durum koduna göre iki hata sınıfı:

| Durum | Sonuç | Davranış |
|---|---|---|
| 401 / 403 / 429 | `BlockedError` | **Tekrar denenmez.** Tekrar denemek yasağı uzatır; o kaynağın kalan sorguları atlanır |
| 5xx | `FetchError` | Geri çekilmeyle tekrar denenir |
| Tanım hatası | `MappingError` | Ağ sorunu değil; kullanıcının girdiği kaynak tanımı yanlış |

`MappingError`'ın ayrı tutulması önemlidir: kullanıcıya "kaynak yanıt vermedi" demek yerine
formda neyi düzeltmesi gerektiği söylenir.

### Kayıt defteri ve kaynak çözümleme

```python
REGISTRY: dict[str, type[BaseSource]]      # kodda tanımlı 11 kaynak

def source_class(name, options):
    if name in REGISTRY:      return REGISTRY[name]      # kodda tanımlı
    if options.get("kind"):   return CustomSource        # panelden eklenmiş
    return None                                          # tanımsız
```

Bu tek fonksiyon sayesinde panelden eklenen kaynak, kodda tanımlı kaynaklarla **aynı** yoldan
geçer: aç/kapat, sorgu listesi, tarama, dedupe, puanlama, kapanan tespiti, kontrol düğmesi.

### Kodda tanımlı kaynaklar

| Kaynak | Yöntem | Anahtar |
|---|---|---|
| Jooble (ABD/global) | REST API | `JOOBLE_API_KEY` |
| Jooble UK | Aynı adapter, `uk.jooble.org` | `JOOBLE_API_KEY_UK` |
| Jooble Almanya | Aynı adapter, `de.jooble.org` | `JOOBLE_API_KEY_DE` |
| freelancermap | Anahtar kelime sayfaları (HTML) | — |
| freelancer.com | Genel API, yetenek kodlarıyla | — |
| Upwork | GraphQL + OAuth2 (otomatik yenilenen token) | `UPWORK_CLIENT_ID` + `SECRET` |
| Reed.co.uk | REST API (HTTP Basic) | `REED_API_KEY` |
| Careerjet | Public API, 7 locale | `CAREERJET_AFFID` |
| Arbeitnow | Ücretsiz açık API | — |
| kariyer.net | HTML | — |
| TED | AB kamu ihale API'si (POST) | — |

### Kodsuz kaynak (`custom.py`)

Panelden tanıtılan kaynak `config["sources"][ad]` altında saklanır ve `kind` alanı taşır:

```yaml
sources:
  ornek-pano:
    kind: rss                        # rss | json
    url: "https://site/feed?q={sorgu}&p={sayfa}"
    items_path: "data.results"       # (json) ilan listesinin yolu
    fields: {title: baslik, url: yol, company: "sirket.ad"}
```

- `{sorgu}` ve `{sayfa}` yer tutucuları doldurulur. **`{sorgu}` yoksa** akış bir kez çekilip
  önbelleğe alınır — aksi halde N sorgu aynı sayfayı N kez indirirdi.
- JSON'da alan yolları noktalıdır (`sirket.ad`), liste öğesine `[0]` ile ulaşılır.
- RSS'te eşleme zorunlu değildir: `title/link/description/pubDate/author` otomatik bulunur.
  Etiket araması büyük/küçük harfe duyarsızdır ve Atom karşılıkları (`published`, `summary`,
  `dc:creator`) sırayla denenir.
- JSON'da `title` ve `url` zorunludur; ikisi olmadan ilan işe yaramaz.

Hata mesajları kullanıcıyı yönlendirecek şekilde yazılmıştır — en kritiği:

> *"175 kayıt bulundu ama hiçbirinden başlık+link okunamadı. Örnek kayıttaki alanlar: slug,
> company_name, title, description…"*

Kullanıcı hangi alan adını yazacağını denemeden bilemez; bu mesaj onu tek adımda çözer.

---

## 4. Tarama akışı (`pipeline.run_scan`)

```
1. Config yüklenir (config.yaml + overlay), puanlayıcı kurulur
2. Aktif kaynaklar belirlenir      (--source ile daraltılabilir)
3. Her kaynak × her sorgu için:
     - anahtar eksikse kaynak atlanır (ağa çıkılmadan)
     - safe_fetch() → (ilanlar, hata)
     - hata mesajı temizlenir (anahtar sızıntısı)
     - runs tablosuna kayıt düşülür
     - BlockedError geldiyse kaynağın kalan sorguları atlanır
4. Toplanan ilanlar tekilleştirilir (dedupe)
5. Açıklamalar geri yüklenir       (kaynak bu tur açıklama vermediyse DB'dekini koru)
6. Puanlanır ve elenenler ayıklanır
7. Çok eski ilanlar filtrelenir     (scan.max_age_days)
8. DB'ye yazılır (upsert)          → yeni / güncellenen sayıları
9. Kapanan ilan tespiti çalışır     (hızlı turda atlanır)
10. Bildirimler üretilir, eskiler budanır
```

### Tekilleştirme

Parmak izi = **başlık + firma**, gürültü kelimeleri atılarak ve Türkçe karakterler
sadeleştirilerek hesaplanır. Aynı ilan iki kaynakta bulunursa tek kayıt tutulur; eksik alanlar
(remote yüzdesi, süre, bütçe) diğer kaynaktan tamamlanır.

### Puanlama

`keywords.yaml` (+ overlay) sürer:

| Aşama | Kural |
|---|---|
| Kapı | `must_any` kelimelerinden biri geçmiyorsa ilan **elenir** |
| Kapı | `hard_exclude` kelimesi geçiyorsa ilan **elenir** |
| Kelime | `boost` ağırlıkları toplanır; **başlıkta** geçen kelime `title_multiplier` ile çarpılır |
| Kelime | `penalty` ağırlıkları düşülür |
| Sinyal | remote / hibrit / yerinde, proje bazlı / kadrolu, tazelik, ülke |

Eşleşme kelime sınırıyla yapılır ("rap" → "raporlama" saymaz). Puan **sınırsız işaretli
tamsayıdır**, 0–100 değildir; panelde 50 puan tam halka olarak gösterilir.

Ağırlık değiştiğinde ağa çıkmaya gerek yoktur: `rescore_all()` kayıtlı bütün ilanları yeniden
hesaplar (~800 satır, milisaniyeler).

---

## 5. İlan hayat döngüsü — kapanan ilanı düşürme

Bu, sistemin en incelikli parçasıdır ve bir hatadan doğmuştur: eskiden "kaynağın listesinde
yok" = kapandı sayılıyordu ve **yayında olan 23 ilan yanlışlıkla kapatılmıştı**. Sebep:
kaynaklar sayfalanmış bir **pencere** döndürür; yeni ilan gelince eski ilan pencereden düşer
ama yayında kalır.

Bugünkü akış `is_active` + `missing_streak` + `verified_at` kolonlarıyla yürür:

```
Kaynakta görüldü      → missing_streak = 0, is_active = 1
Görülmedi             → missing_streak++   (ilan "şüpheli", panelde rozet)
missing_streak >= 2   → ilanın LİNKİ AÇILIR
                          ├─ 404/410 ya da "no longer available" → KAPAT + bildirim
                          ├─ sayfa açılıyor                      → sayacı sıfırla (yanlış alarm)
                          └─ bağlantı kurulamadı                 → karar verme, sonraki tura bırak
Link doğrulanamıyorsa → 6 tur üst üste görülmezse kapat (son çare)
Kaynağa geri döndü    → otomatik yeniden aç
```

İki koruma:

- **Hata veren kaynağın ilanları kapatılmaz.** Site çöktü diye ilan kapanmış sayılmaz;
  yalnızca sorunsuz çalışan kaynaklar için kayıp sayımı yapılır.
- **Hızlı tur kayıp saymaz.** Kapsamı kısmi olduğu için orada görülmeyen ilan şüpheli olmaz.

### Jooble — link yerine API ile doğrulama

Jooble ilan sayfaları her koşulda 403 döner (Cloudflare TLS parmak izi); `/desc/`, `/away/`
ve `/jdp/` biçimlerinin üçü de, tarayıcı `User-Agent`'ı ile bile. Aktif ilanların %29'u bu
kaynaktan geldiği için burası uzun süre kör noktaydı: kapatma kararı "6 tur üst üste
listede yok" tahminine dayanıyordu.

Çözüm, ilanın **kendi başlığıyla API'ye sorulmasıdır** (`JoobleSource.verify_alive`):

```
Şüpheli Jooble ilanı → POST /api/<key> {keywords: <ilan başlığı>, ResultOnPage: 100}
                          ├─ parmak izi dönen kayıtlar arasında  → YAYINDA (sayaçlar sıfırlanır)
                          ├─ yok (1. kez)                        → api_miss_streak = 1, kapatma YOK
                          ├─ yok (2. kez)                        → KAPAT + bildirim
                          └─ API hata/kota                       → karar verme
```

**`id` ile eşleştirilmez.** Ölçüldü: Jooble aynı ilana (aynı başlık, aynı firma) sorgudan
sorguya **farklı `id`** veriyor, yani sakladığımız `source_id` doğrulama için kararsız bir
anahtar. Eşleştirme `dedupe.fingerprint()` (başlık + firma) ile yapılır — bu zaten sistemin
her yerinde kullanılan kararlı anahtar.

İki tur şart koşulmasının nedeni ölçümdür: başlık araması ilanı her zaman getirmiyor
(20 canlı ilanda 19 isabet, %95). Tek bir bulunamamayla kapatmak yayında olan ilanı
düşürebilirdi; iki tur şartı yanlış kapatma olasılığını ~%0,25'e indiriyor. Sayaç
`missing_streak`ten ayrıdır (`api_miss_streak`): biri "genel aramada görünmedi", diğeri
"kendi başlığıyla soruldu, indekste yok" demektir — ikincisi çok daha güçlü bir kanıttır.

Ayarlar: `freshness.api_verify_limit` (60/tur), `api_verify_miss_threshold` (2). Arayüz
`BaseSource.can_verify` + `verify_alive()`; başka bir kaynak bot duvarına takılırsa aynı
kancaya bağlanır. Cloudflare'ı TLS taklidiyle aşmak bilinçli olarak kapsam dışıdır.

Panelde her satırdaki **"Artık Aktif Değil"** düğmesi yine durur: %95 isabetli otomatik
doğrulamanın kaçırdığı durumda en kesin yol kullanıcının kendisidir.

### Temizlik turu — "Listeyi kontrol et"

Yukarıdaki akış sıraya dayanır: tur başına 80 ilan, ilan başına en az 12 saat ara. Büyük
listede bir ilana sıra günler sonra gelir, o yüzden panelde **elle** başlatılan bir tur
vardır (`sweep.py`): kullanıcının o an filtrelediği ilanların linkini paralel (8 istek)
açar, kapananları kapatır. Sıraya bakmaz, kaynak atlamaz — Jooble de denenir, 403 gelirse
sonuç **"doğrulanamadı"** olarak raporlanır (kapatılmaz, sorunlu da işaretlenmez) ve
`verified_at` YAZILMAZ: yoksa ilan "bakıldı" sayılıp otomatik sıranın sonuna atılırdı.

İlerleme `/api/durum` yanıtına biner (ayrı poller yok); panelde canlı şerit `142 / 214`
gösterir, bitince toast özet verir. Ayarlar: `freshness.sweep_max` / `sweep_workers` /
`sweep_timeout`. Yetki: `sweep_links` (varsayılan olarak kapalı, ağ trafiği üretir).
CLI karşılığı `py -m scanner sweep`.

**Kritik ayrıntı — neden `probe()`:** `HttpClient.request()` 404'te `FetchError`, 403'te
`BlockedError` fırlatır; durum kodu çağırana hiç ulaşmaz. Link kontrolü uzun süre bunu
kullandığı için her 404 "karar verilemedi" sayılıyor, **silinmiş ilan hiçbir zaman
kapatılmıyordu** — kapanma yalnızca sayfa metnindeki "no longer available" gibi
ifadelerle yakalanabiliyordu. `HttpClient.probe()` tek deneme yapar, hata fırlatmaz ve
yanıtı olduğu gibi verir; `_inspect_link()` artık onu kullanır ve `LinkVerdict` döner.

---

## 6. Zamanlayıcı — iki tempo

`BackgroundScanner` tek bir iş parçacığında döner (iki tarama aynı anda aynı SQLite dosyasına
yazmaz):

| Tur | Varsayılan | Ne yapar |
|---|---|---|
| **Hızlı** | 8 dk | Yalnızca hızlı kaynakların ilk sayfası — yeni ilan avı. Kapanan tespiti yok |
| **Tam** | 30 dk | Tüm kaynaklar + tüm sorgular + kapanan tespiti + link doğrulaması |

Durum `/api/durum` ucundan okunur: faz, sonraki tura geri sayım, son turun sayıları, hatalar.

Ayarlardan yapılan değişiklikler **yeniden başlatmadan** geçerli olur:

- `reconfigure()` aralıkları kilit altında günceller ve uykuyu böler.
- Aç/kapat iş parçacığını **durdurmaz, duraklatır** (`set_enabled`). Masaüstü uygulaması
  zamanlayıcıyı kendi yönetir (`external_scheduler`); panel onu durdurmamalıdır.
- **"Şimdi tara" duraklatılmışken de çalışır** — düğmeye basan kullanıcı taramayı istiyordur.

---

## 7. Depolama

SQLite, üç tablo:

| Tablo | İçerik |
|---|---|
| `projects` | İlanlar (birincil anahtar: `fingerprint`) |
| `runs` | Her kaynak-sorgu çalıştırması: bulunan sayısı, durum, hata |
| `notifications` | Yeni ilan / kapandı bildirimleri |

### Tarihler

Bütün zaman damgaları **sabit `+00:00` ekli ISO-8601 UTC metinleridir**. Bu bilinçli bir
karardır: sözlükbilimsel karşılaştırma kronolojik karşılaştırmaya eşittir, dolayısıyla tarih
filtreleri düz `>=` / `<` ile çalışır.

- `posted_at` — ilanın yayın tarihi (boş olabilir)
- `first_seen_at` — sisteme düşme anı (hiç boş olmaz)
- Sıralama ve filtrelerde `COALESCE(posted_at, first_seen_at)` kullanılır.

### Bütçe

`budget_raw` serbest metindir ("$100–110/saat", "€600/Tag", "1.200 TL/gün"). Sıralanabilmesi
için iki türetilmiş kolon yazılır:

- `budget_amount` — metinden çıkarılan tutar (aralıkta **üst uç**)
- `budget_daily` — karşılaştırılabilir günlük USD tahmini (saat×8, gün×1, hafta/5, ay/22, yıl/260)

Periyot yazmayan tutarlar 20 bin – 1 milyon aralığındaysa **yıllık maaş** sayılır (`$100k - $150k`
en yaygın biçimdir ve periyot yazmaz); ihale toplamları bu kuraldan muaftır. Götürü fiyatlı
işlerde `budget_daily` boş kalır — saatlik $110 ile götürü $5.000'i aynı kolonda sıralamak
yanıltıcı olurdu.

### Sıralama

`ORDERS` bir **beyaz listedir**; bilinmeyen değer sessizce skora düşer, dolayısıyla sıralama
parametresi SQL enjeksiyonuna kapalıdır.

| Grup | Seçenekler |
|---|---|
| Uygunluk | skor (iki yön), çalışma şekli önceliği (uzaktan > hibrit > belirsiz > yerinde) |
| Bütçe | yüksekten / düşükten — bütçesi bilinmeyen iki yönde de sonda |
| Tarih | ilan tarihi (iki yön), sisteme düşme sırası |
| Diğer | firma adı |

Her sıralama `fingerprint` ile biter: eşit değerlerde SQLite satır sırası kararsızdır ve
sayfalamada aynı ilan iki sayfada çıkabilirdi.

### Şema göçü

Yeni kolonlar `MIGRATIONS` sözlüğüyle eklenir, indeksler migration'dan **sonra** kurulur (eski
veritabanında kolon yoksa "no such column" alınıyordu). Eski veritabanı silinmeden çalışmaya
devam eder; bütçe kolonları ilk açılışta bir kez geriye dönük doldurulur.

---

## 8. Yapılandırma ve ayarlar

### Katmanlı yapılandırma

```
config/config.yaml        ← taban, kod ile gelir, yorumlarla belgelenmiş
config/settings.local.yaml ← panelden yazılır, tabanın üzerine uygulanır
```

`config.yaml` **asla yeniden yazılmaz**: içindeki yorumlar her kaynağın neden öyle
ayarlandığını belgeler, YAML round-trip'i hepsini silerdi. Aynı ikili `keywords.yaml` /
`keywords.local.yaml` için de geçerlidir.

Birleştirme kuralı (`deep_merge`), üç dal:

1. dict + dict → özyineli birleştir
2. overlay değeri `None` → anahtarı **sil** (mezar taşı)
3. liste / skaler → tamamen değiştir

Mezar taşı şarttır: kullanıcı bir kelimeyi listeden çıkardığında düz birleştirme onu tabandan
geri getirirdi.

Kaydederken `prune_unchanged()` tabanla **aynı** olan değerleri eler. Bu olmasa panel her
kaydedişte bütün listeleri kopyalar, varsayılanlar donar ve sonraki sürümde iyileştirilen
sorgu listesi kullanıcıya hiç ulaşmazdı.

`load_config()` önbelleksizdir; kaynak ve sorgu değişiklikleri bir sonraki turda kendiliğinden
geçerli olur.

### Sırlar

API anahtarları `.env` dosyasında tutulur (git'e girmez). `save_env()` satır sırasını ve
yorumları korur, ardından `_load_dotenv(force=True)` ile **çalışan sürecin ortamını tazeler** —
normal yükleme `os.environ.setdefault` kullandığından değişen anahtar aksi halde ancak yeniden
başlatınca görülürdü.

Anahtar hiçbir zaman tarayıcıya basılmaz; yalnızca maskesi gösterilir (`94••••••8b`). Boş
bırakılan alan mevcut anahtarı değiştirmez.

**Sızıntı önlemi:** Jooble anahtarı URL yolunda gider ve hata mesajı URL'yi içerir; bu mesaj
`runs` tablosuna ve panele düşüyordu. `scrub()` bilinen anahtar değerlerini `***` ile
değiştirir ve hem kontrol çıktısına hem hata kaydına uygulanır.

### Dosya yolları

```
bundle_dir()   okunacak dosyalar   → config/*.yaml, şablonlar, statik dosyalar
data_dir()     yazılacak dosyalar  → veritabanı, loglar, .env, overlay, çıktılar
```

Geliştirmede ve sunucuda ikisi de proje klasörüdür — **ayarlar klasörle birlikte taşınır**.
Kurulu exe'de `data_dir()` `%LOCALAPPDATA%` altına düşer, çünkü Program Files yazılabilir
değildir.

---

## 9. Web paneli

FastAPI + Jinja2, sunucu tarafında render. Filtreleme **düz bir GET formudur**; JavaScript
yalnızca iki iş yapar: canlı durum yoklaması ve kaynak kontrol düğmesi.

### Rotalar

| Rota | İş |
|---|---|
| `GET /` | İlan listesi: filtreler, sıralama, sayfalama |
| `GET /bildirimler` | Bildirim akışı |
| `GET /ayarlar` | Ayarlar ekranı |
| `POST /ayarlar/ne-ariyoruz` | Sorgular + kelimeler + ağırlıklar → kaydet ve yeniden puanla |
| `POST /ayarlar/kaynaklar` | Kaynak aç/kapat, sayfa, listeler |
| `POST /ayarlar/kaynak-ekle` | Kodsuz kaynak ekleme/düzenleme |
| `POST /ayarlar/kaynak-sil` | Panelden eklenmiş kaynağı silme |
| `POST /ayarlar/anahtarlar` | API anahtarları |
| `POST /ayarlar/tarama` | Aralıklar, otomatik tarama |
| `POST /api/kaynak-kontrol` | Kaynağı canlı deneme (JSON) |
| `GET /api/durum` | Canlı tarama durumu (10 sn'de bir yoklanır) |
| `POST /status`, `/kapandi`, `/tara` | Satır aksiyonları ve elle tarama |

Ayarlar formları kaydettikten sonra `303` ile geri döner; bildirim mesajı query parametresiyle
taşınır (oturum ara katmanı yoktur).

### "Veriyi kontrol et"

`probe.py` kaynağı canlı dener ve kaç ilan geldiğini, ilk üç başlığı, süreyi gösterir.

- **Veritabanına yazmaz.** Modül `Storage`'ı hiç import etmez — dry-run yapısal olarak
  garantidir ve test bunu doğrular.
- Tek sorgu, tek sayfa, tek deneme, hız sınırı kapalı. Tarama istemcisinin 3 deneme + geri
  çekilmesi bir kontrolü 14+ saniyeye çıkarırdı.
- Kısaltma yalnızca **her elemanı ayrı istek olan** listelere uygulanır. Tek isteğe sığan kod
  listeleri kısaltılmaz — aramayı daraltıp yanlış "sonuç yok" üretirdi.
- Anahtar eksikse ağa çıkılmadan söylenir.
- "Yeni kaynak" formundan gelen tanım **kaydedilmeden** denenebilir.
- Eşzamanlı istekler bir semaforla sınırlanır; tarama sürüyorsa yanıta uyarı eklenir.

### Rota sync mi async mi

Kontrol ucu bilinçli olarak `def`'tir (`async def` değil): httpx senkron çalışır ve hız sınırı
`time.sleep` kullanır. Senkron fonksiyon FastAPI tarafından iş parçacığı havuzuna atılır ve
olay döngüsünü kilitlemez.

---

## 10. Diğer arayüzler

**Komut satırı** (`cli.py`): `scan`, `export`, `mail`, `verify`, `notifications`, `rescore`,
`upwork-auth`, `serve`, `watch`, `stats`. Panelden bağımsız çalışır.

**Masaüstü** (`desktop/`): terminal bilmeyen kullanıcı için Tkinter penceresi ve tepsi ikonu.
Başlat/Durdur, Paneli Aç, Şimdi Tara düğmeleri; canlı log ve sayı kartları. Pencere
kapatılınca tepsiye iner, tarama devam eder. Kendi zamanlayıcısını kurar ve panele
`external_scheduler` olarak geçirir — böylece panelin canlı göstergesi doğru çalışır.

**Çıktılar**: Excel/CSV (`export.py`), SMTP HTML rapor (`mailer.py`).

---

## 11. Test yaklaşımı

144 test, **hiçbiri ağa çıkmaz**. Kaynak testleri kaydedilmiş yanıt örnekleriyle (fixture)
çalışır; kodsuz kaynak testleri sahte bir HTTP istemcisi kullanır ve istenen adresleri
doğrular.

Testlerin bir kısmı davranış değil **sözleşme** sabitler:

- `config.yaml` baytları kaydettikten sonra değişmemiştir (yorum koruma garantisi)
- Kontrol sırasında veritabanı dosyası oluşmamıştır (dry-run garantisi)
- Her sıralama ifadesi `fingerprint` ile biter (sayfalama tutarlılığı)
- Ayarlar ekranını açıp değiştirmeden kaydetmek ayarları bozmaz (round-trip güvenliği)
- Maskelenmiş anahtar hiçbir zaman tam değeri döndürmez

---

## 12. Bilinen sınırlar

| Sınır | Sebep |
|---|---|
| Jooble linkleri doğrulanamıyor | Cloudflare TLS parmak izi; header'la aşılamaz. Elle "Artık Aktif Değil" düğmesi telafi eder |
| kariyer.net'te düşük sayfa sayısı | ~35 istekte IP bazlı 403, yasak ~10 dk |
| Kodsuz kaynak yalnızca RSS/JSON | HTML kazıma her site için ayrı seçici gerektirir; kodsuz kapsam dışı |
| Bütçe çevrimi yaklaşıktır | Kurlar sabittir; sıralama için yeterli, muhasebe için değil |
| Kod imzalama sertifikası yok | Kurulu exe ilk çalıştırmada SmartScreen uyarısı verir |
| Gömülü anahtarlar çıkarılabilir | Paketlenmiş exe içinden teknik olarak okunabilir |

---

## 13. Notlar

Uygulamanın görünen adı **İlan Tarayıcı**'dır (panel başlığı, tarayıcı sekmesi, masaüstü
penceresi). Veri klasörünün adı (`%LOCALAPPDATA%\SAP Proje Radari`) bilinçli olarak
değiştirilmemiştir: değiştirilirse kurulu programların mevcut veritabanı ve ayarları öksüz
kalırdı. Yeniden adlandırma istenirse taşıma adımıyla birlikte yapılmalıdır.
