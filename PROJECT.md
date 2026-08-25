# SAP Proje Radarı

Şirketin **proje arayışı** için: internetteki freelance/contract iş panolarından **SAP & ABAP projelerini**
toplar, uzaktan (remote) çalışılabilenleri en üste çıkarır. Çıktılar: SQLite, Excel/CSV, e-posta raporu,
20'şerli sayfalanan yerel web paneli.

Öncelik sırası: **remote + proje bazlı > hibrit > yerinde**.

> Proje geçmişi: ilk gün "iş ilanı tarayıcı", sonra "freelancer bulucu" olarak kuruldu; amaç netleşince
> (2026-08-17) **contract/remote SAP projesi avına** çevrildi. Aday bulma tarafı kaldırıldı.

## Hızlı başlangıç

```bash
py -m pip install -r requirements.txt
```

```bash
$env:PYTHONPATH="src"; py -m scanner scan
```

```bash
$env:PYTHONPATH="src"; py -m scanner serve
```

Panel: http://127.0.0.1:8000 — sayfa başına 20 ilan, altta `1 2 3 …` sayfalama.

**Veri sürekli akar (iki tempo):**

| Tur | Sıklık | Ne yapar |
|---|---|---|
| **Hızlı tur** | 8 dk | Sadece hızlı kaynakların ilk sayfası (jooble, freelancer.com) — yeni ilan avı. Kapanan tespiti **yapmaz** (kapsam kısmi). |
| **Tam tur** | 30 dk | Tüm kaynaklar + tüm sorgular, kapanan tespiti, her 2. tam turda link doğrulaması. |

Üst barda canlı gösterge (faz + sonraki tura geri sayım) ve **"Şimdi tara"** düğmesi vardır; yeni ilan
düşerse sayfa altında **"N yeni ilan geldi"** şeridi çıkar (liste okurken kaymaz).

**Panelden bağımsız çalıştırma:** `run_agent.ps1` (panel + tarama) veya `py -m scanner watch`
(panelsiz). Windows açılışına bağlamak: `install_agent.ps1` (kaldırmak: `-Remove`).

## Komutlar

| Komut | Ne yapar |
|---|---|
| `py -m scanner scan` | Tüm aktif kaynakları tarar, puanlar, veritabanına yazar |
| `py -m scanner scan --source jooble --dry-run` | Tek kaynak, DB'ye yazmadan dener |
| `py -m scanner export --remote --contract` | Sadece uzaktan + proje bazlı ilanlar → Excel |
| `py -m scanner export --csv --new` | Sadece yeni ilanlar, CSV |
| `py -m scanner export --shortlist` | Takibe alınanlar |
| `py -m scanner export --include-supply` | Fiverr hizmet (arz) ilanlarını da yaz |
| `py -m scanner mail --dry-run` | Mail önizlemesi üretir, göndermez |
| `py -m scanner verify` | Aktif ilanların linkini açar, kapananları işaretler |
| `py -m scanner notifications [--unread]` | Bildirim akışı (terminalde) |
| `py -m scanner notifications --read-all` | Hepsini okundu işaretle |
| `py -m scanner upwork-auth` | Upwork OAuth2 token'ını alır (bir kez); `--status` durumu gösterir |
| `py -m scanner rescore` | `keywords.yaml` değişince ağa çıkmadan yeniden puanlar |
| `py -m scanner serve` | Yerel panel + arka planda otomatik tarama |
| `py -m scanner serve --no-auto` | Panel, otomatik tarama kapalı |
| `py -m scanner serve --interval 15` | Otomatik tarama aralığını (dk) geçici değiştir |
| `py -m scanner watch [--interval 30]` | Panelsiz sürekli tarama döngüsü (terminalde bırakılır) |
| `py -m scanner stats` | Özet + son çalıştırmalar |

## Kaynaklar (son kontrol: 2026-08-17, canlı test)

| Kaynak | Yöntem | Getirdiği |
|---|---|---|
| **Jooble** (`jooble`, `jooble-uk`, `jooble-de`) | Resmi REST API, **bölge başına ayrı anahtar** | **En verimli kaynak.** Her alan adı kendi anahtarını ister — `jooble.org` anahtarı `uk`/`de` üzerinde 403 verir (2026-08-24, üç anahtarla doğrulandı). Bu yüzden bölgeler ayrı kaynak sınıfıdır: kendi anahtarı, kendi sorguları, ilanlarda kendi `source` etiketi, panelde kendi kartı. **ABD/global:** 62.925 sonuç ama alakasız ilan oranı yüksek. **UK:** 9.018 sonuç, ölçümde 100/100 isabet (Next Ventures, RED, Vivid gibi SAP contract ajansları). **Almanya:** 65.043 sonuç — ABD'den bile fazla; AB SAP freelance pazarının merkezi. Almanca sorgular (`SAP Berater freiberuflich`) yalnızca burada anlamlı, daha önce global indekste boşa gidiyordu. Anahtar değişimi: Ayarlar > Anahtarlar. Aynı ilan iki bölgede çıkarsa dedupe tek kayda indirir. Sorgu başına 100 ilan (5 sayfa × 20). "SAP ABAP remote/contract/freelance" sorguları ile ABD + AB uzaktan ilanları, çoğunda maaş aralığı. Anahtar **global indekse** bağlı; `tr.jooble.org` aynı anahtarla 403 verir (TR indeksi için ayrı anahtar gerekir). |
| **freelancermap** (`freelancermap`) | Anahtar kelime sayfaları: `/projects/sap-abap`, `/projects/sap`, `/projects/remote` … | **Avrupa contract pazarının merkezi.** 113 ilan. Kartta **remote yüzdesi**, "Freelance" tipi, **süre** ("5 months+"), **başlangıç ayı** hazır geliyor — puanlamanın en sağlam verisi. Site araması JS ile çalıştığı ve `?page=` yok sayıldığı için sayfalama yerine çoklu anahtar kelime sayfası taranır. |
| **freelancer.com** (`freelancercom`) | Resmi genel API, anahtar gerekmiyor | Küçük/orta bütçeli uzaktan işler (7 aktif SAP projesi). Bütçe aralığı ve para birimi geliyor. ABAP'ın kendi yetenek kodları: 1733, 1734, 1784; SAP ailesi: 267, 1438, 1489, 1532, 1533. |
| **Arbeitnow** (`arbeitnow`) | Ücretsiz açık API | Almanya/AB ilanları, `remote` alanı güvenilir. SAP hacmi düşük (~13) ama bedava. |
| **kariyer.net** (`kariyernet`) | HTML: `/is-ilanlari?kw=…&cp=…` | Türkiye pazarı — çoğu yerinde/kadrolu, hibrit olanlar işe yarar. **Bot koruması:** ~35 istekte IP bazlı 403, yasak ~10 dk → `pages: 2`. |
| **Upwork** (`upwork`) | Resmi GraphQL API + OAuth2 (otomatik yenilenen token) | **API başvurusu onaylandı (2026-08-18), aktif.** Public yolların hepsi kapalı (RSS/JSON 410, arama 403 Cloudflare, GraphQL 401) — sadece anahtarla çalışır. Kurulum: `.env` → `UPWORK_CLIENT_ID` + `UPWORK_CLIENT_SECRET`, sonra bir kez `py -m scanner upwork-auth`. Access token ~24 saatte dolar; `sources/upwork_auth.py` süresi dolmadan yeniler, 401 gelirse isteği yenilenmiş token'la tekrarlar. Kimlik bilgisi girilmemişse kaynak sessizce atlanır. |
| **Fiverr** (`fiverr`) | Arama sayfasındaki `#perseus-initial-props` JSON'u | **ARZ TARAFI — varsayılan kapalı.** Fiverr'da iş veren talepleri (Buyer Requests / Briefs) 2023'te public olmaktan çıktı; `/requests` ve `/briefs` login + PerimeterX arkasında. Erişilebilen tek veri satıcı hizmet ilanları: ABAP işi *arayan* değil, ABAP hizmeti *satan* kişiler. Fiyat/rakip referansı olarak kullanılabilir (5–150 USD paket fiyatları). Kayıtlar `is_supply = 1` ile işaretlenir. |
| **Reed.co.uk** (`reed`) | Resmi REST API, ücretsiz anahtar (HTTP Basic) | UK contract pazarı — SAP için Avrupa'nın en derin ikinci pazarı. Günlük ücret aralığı + contract/temp bayrakları. `.env` → `REED_API_KEY` (https://www.reed.co.uk/developers). |
| **Careerjet** (`careerjet`) | Public API, ücretsiz ortak kimliği | 90 ülke agregatörü; 7 locale taranıyor (en_GB, de_DE, de_AT, de_CH, nl_NL, en_US, tr_TR). `.env` → `CAREERJET_AFFID` (https://www.careerjet.com/partners/api/). |
| **TED** (`ted`) | AB kamu ihale API'si, anahtarsız (POST) | **İş ilanı değil, ihale.** 98 aktif SAP ihalesi (ör. "Migration SAP S/4HANA" 1,38 M€, "ABAP developer" alımları). CPV 72*/48* + ifade bazlı sorgu; `engagement = "ihale"`, panelde mor **İHALE** rozeti. GET 405 verir, sorgu POST gövdesinde gider; ifadeler OR ile birleştirilemediği için her ifade ayrı sorgu. |
| WeWorkRemotely / Workana / freelance.de | — | Cloudflare / 403. |
| PeoplePerHour | — | **Adapter yazıldı, test edildi, silindi.** `?q=sap` parametresi filtrelemiyor: dönen 20 ilan Shopify/Mailchimp/MEP mühendisliği. Sahte isabet üretmemek için kaldırıldı. |
| Guru | — | Proje listesi JS ile yükleniyor, statik HTML'de kart yok. |
| twago / SOLCOM / Etengo / freelance-info | — | Cloudflare veya captcha duvarı. |
| Remotive / Himalayas / WorkingNomads / RemoteOK | — | Genel remote panoları; SAP/ABAP sonucu sıfır. Remotive 2026-08-19'da tekrar denendi: `search=SAP` filtrelemiyor, dönen 17 ilanın hiçbiri SAP değil. |
| Arbeitsagentur (Alman İş Kurumu) | — | 2026-08-19: API 403; kimlik doğrulaması değişmiş. Almanya en büyük SAP contract pazarı olduğu için ileride tekrar denenebilir. |
| gulp.de / jobdataapi | — | gulp REST'i 401 (giriş şart), jobdataapi ücretli abonelik istiyor. |
| justjoin.it / NoFluffJobs | — | API yolları değişmiş (404/405); Polonya pazarı, düşük öncelik. |
| LinkedIn | — | ToS scraping'i yasaklıyor, kapsam dışı. |

## Güncellik: kapanan ilanı listeden düşürme

> **2026-08-17 düzeltmesi:** eskiden "kaynağın listesinde yok" = kapandı sayılıyordu ve
> **yayında olan 23 ilan yanlışlıkla kapatılmıştı** (linkleri 200 dönüyordu). Sebep: kaynaklar
> sayfalanmış bir **pencere** döndürüyor — yeni ilan gelince eski ilan pencereden düşüyor ama
> yayında kalıyor. Artık yokluk tek başına kapatmıyor.

İlan hayat döngüsü `is_active` + `missing_streak` + `verified_at` ile takip edilir:

1. Kaynakta **görülen** ilan: `missing_streak = 0`, `is_active = 1`.
2. **Görülmeyen** ilanın sayacı artar → ilan **şüpheli** olur (panelde "KONTROL EDİLİYOR" rozeti).
3. Sayaç `freshness.suspect_after_missing_scans` (**2**) eşiğini aşınca ilanın **linki açılır**:
   - 404/410 ya da sayfada "no longer available / yayından kaldırıldı" → **kapatılır** + bildirim.
   - Sayfa hâlâ açılıyorsa → **sayaç sıfırlanır** (yanlış alarm, ilan yayında).
   - Bağlantı kurulamadıysa → karar verilmez, bir sonraki tura kalır.
4. Link kontrolü yapılamayan kaynaklarda (bot duvarı, `verify_skip_sources`) son çare:
   `close_unverifiable_after_missing_scans` (**6**) tur üst üste görülmezse kapatılır.
5. İlan kaynağa geri dönerse otomatik yeniden açılır.

Diğer korumalar:
- **Hata veren kaynağın ilanları kapatılmaz.** Site çöktü ya da bizi engelledi diye ilanlar
  kapanmış sayılmaz; yalnızca sorunsuz çalışan kaynaklar için kayıp sayımı yapılır.
- **Hızlı tur kayıp saymaz.** Kapsamı kısmi olduğu için orada görülmeyen ilan şüpheli olmaz.

### Jooble: linkler doğrulanamıyor, ilanlar API ile doğrulanıyor

> **Güncelleme (2026-08-23):** aşağıdaki sınır hâlâ geçerli — linkler açılamıyor. Ama artık
> kör değiliz: şüpheli Jooble ilanı **kendi başlığıyla Jooble API'sine sorulur** ve
> **başlık+firma parmak iziyle** (`dedupe.fingerprint`) eşleştirilir. `id` ile eşleştirme
> yapılamaz — ölçüldü, Jooble aynı ilana sorgudan sorguya farklı `id` veriyor. Başlık
> araması ilanı %95 oranında getiriyor (20 canlı ilanda 19), o yüzden tek bulunamama
> kapatmaz: üst üste **iki tur** gerekir (`freshness.api_verify_miss_threshold`). Tur başına
> 60 ilan doğrulanır. Detay: MIMARI.md § "Jooble — link yerine API ile doğrulama".
>
> Aşağıdaki "hiçbir zaman fark edemeyiz" tespiti bu yüzden artık geçerli değil: Jooble
> indeksinden düşen ilan iki turda kapanır. Elle bildirim düğmesi son çare olarak durur.

### Bilinen sınır: Jooble linkleri hiç doğrulanamıyor

Jooble ilan sayfaları (`/desc/…`, `/away/…`) **her koşulda 403 döner** — tam tarayıcı
User-Agent'ı, `Accept`/`Accept-Language`/`Referer` başlıklarıyla bile (2026-08-19'da canlı
test edildi). Bu, header'la aşılabilecek basit bir kontrol değil; Cloudflare TLS/istek
parmak izine bakıyor. Sonuç: **Jooble linkleri otomatik doğrulanamıyor** ve bu kaynak
`freshness.verify_skip_sources`'ta kalıcı olarak atlanıyor.

#### Denenip elenen yollar (2026-08-23) — tekrar denemeyin

`curl_cffi` ile TLS parmak izi taklidi **çalışmıyor**. Ölçüm:

| Hedef | Denenen `impersonate` | Sonuç |
|---|---|---|
| `jooble.org/desc/<id>` (kapalı ve açık ilan) | chrome, chrome124, chrome146, chrome150, firefox147, safari17_0, safari260 | 7/7 → **403** |
| `jooble.org/` anasayfa | aynısı | **403** — ana sayfa bile geçmiyor |
| `upwork.com/nx/search/jobs/` | aynısı | 7/7 → **403**, 344 KB'lik "Challenge - Upwork" sayfası (Cloudflare Turnstile) |
| Oturum ısıtma (anasayfa → çerez → `Referer` ile hedef) | chrome146/150/firefox147 | Upwork anasayfası 200 + 10 çerez, **arama sayfası yine challenge** |

Neden: engel yalnızca TLS parmak izi değil, **JavaScript çalıştırmayı zorunlu kılan
Turnstile challenge**. `curl_cffi` JS çalıştırmadığı için bu duvarı prensip olarak
aşamaz — daha yeni bir parmak izi denemek de bir şey değiştirmez.

**Playwright (gerçek tarayıcı motoru) de çalışmıyor.** Headless Chromium ile, `navigator.
webdriver` gizlenmiş, gerçek viewport/locale/timezone verilmiş halde: Jooble ve Upwork
hedeflerinin üçü de **403 "Just a moment…"** — 24 saniye beklemeye rağmen challenge
çözülmedi. Cloudflare headless imzasını tanıyor. Görünür (headful) pencere muhtemelen
geçerdi (kullanıcının kendi Chrome'u geçiyor), ama bu, arka planda sürekli açık bir
masaüstü oturumu ve ekranda açılıp kapanan tarayıcı demek — otomatik tarayıcı için
uygun değil. `playwright-stealth` / `undetected-chromedriver` sınıfı tespit-atlatma
araçları bilinçli olarak kapsam dışı bırakıldı: kırılgan, bakım yükü yüksek ve açıkça
bot korumasını dolanma niteliğinde.

Üç katman denendi ve elendi: **başlık taklidi → TLS parmak izi taklidi → gerçek tarayıcı
motoru.** Ücretsiz kazıma yolu kapalı.

Geriye kalanlar: ücretli sağlayıcı (Bright Data Web Unlocker, ~2 $/ay — hem Upwork hem
Jooble kapanma tespitini çözer) ya da Upwork için **iş uyarısı e-postaları + IMAP**:
ücretsiz hesap yeterli, ToS'a uygun, kazıma yok, ban riski yok (Upwork'ün geliştirici
API'si $25.000 kazanç şartı nedeniyle erişilemez). Jooble tarafında bugünkü mekanizma
(API doğrulaması + elle bildirim düğmesi) yürürlükte kalır.

Pratik etkisi: Jooble bir ilanı kendi arama sonuçlarında döndürmeye devam ettiği sürece
(yani `missing_streak` hiç artmadığı sürece) — asıl işveren sitesinde ilan kapanmış olsa
bile biz bunu **hiçbir zaman** kendiliğinden fark edemeyiz. Jooble kendi indeksini ne
zaman tazeleyeceğine bağlıyız; bu bizim kontrolümüzde değil.

**Kullanıcı çözümü:** panelde her aktif ilan satırında **"Artık Aktif Değil"** düğmesi var
(`storage.report_closed()` → `POST /kapandi`). Linke tıklayıp "no longer available" görünce
tek tıkla ilanı kapatır, "kapandı" bildirimi üretir — otomatik doğrulamanın ulaşamadığı
boşluğu kapatan elle mekanizma budur.

`py -m scanner verify` tamamlayıcı kontroldür: aktif ilanların linkini açar, 404/410 dönen ya da
sayfasında "no longer available / yayından kaldırıldı" geçen ilanları kapatır. Kontrol **sırayla**
ilerler: her ilanın `verified_at` damgası tutulur, sıradaki turda en uzun süredir bakılmamış ilanlar
seçilir (erişilemeyen link de "bakıldı" sayılır, yoksa aynı ilanlara takılıp kalınırdı).
`freshness.verify_skip_sources` (varsayılan `["jooble"]`) bot duvarına takılan kaynakları eler —
oradan hep 403 döner, bilgi vermez; o kaynağın ilanları zaten listeden düşünce kapatılır.

**Sürekli akış (`auto_scan`):** panel açıkken tarama + doğrulama arka planda kendiliğinden döner.

| Ayar | Varsayılan | Ne yapar |
|---|---|---|
| `auto_scan.enabled` | `true` | Panelde arka plan taraması |
| `auto_scan.interval_minutes` | `30` | Tam tur aralığı (en az 5) |
| `auto_scan.quick_interval_minutes` | `8` | Hızlı tur aralığı |
| `auto_scan.quick_sources` | `[jooble, freelancercom]` | Hızlı turda taranan kaynaklar |
| `auto_scan.run_on_start` | `true` | Panel açılır açılmaz bir tarama |
| `auto_scan.verify_every_n_scans` | `2` | Her N taramada bir link doğrulaması |
| `auto_scan.verify_limit` | `40` | Turda kaç ilanın linki açılır |

Tek iş parçacığı kullanılır: iki tarama aynı anda aynı SQLite dosyasına yazmaz.
Durum `/api/durum` ucundan okunur (faz, geri sayım, son turun yeni/güncel/kapanan sayıları, hatalar).

**Tarih hatası (düzeltildi, 2026-08-17):** Jooble ISO tarih (`2026-08-10`) döndürüyor, tarih
çözücü `dayfirst=True` ile bunu 8 Ekim yapıyordu. İlanlar **gelecek tarihli** görünüp "taze"
sayılıyor, eski ilanlar listenin üstünde kalıyordu. Artık ISO metinler önce `fromisoformat` ile
çözülür; kalan formatlarda gün-önce okunuşu geleceğe düşerse gün/ay takası denenir.

## Bildirimler

`notifications` tablosu + panelde **Bildirimler** sekmesi (üstte okunmamış sayısı rozeti):

- **Yeni ilan** bildirimi: puanı `notifications.min_score` (varsayılan 15) üstündeki her yeni ilan.
- **Kapandı** bildirimi: yukarıdaki kurallarla kapanan ilanlar.
- Rozet panelde 60 saniyede bir arka planda tazelenir (`/api/unread`), sayfayı yenilemeye gerek yok.
- `notifications.keep_days` (30) geçen bildirimler tarama sonunda otomatik silinir.
- Terminalden: `py -m scanner notifications --unread`.

## Arz / talep ayrımı

Fiverr gibi kaynaklar "hizmet satan" ilanlar döndürür. Bunlar `Project.is_supply = True` ile
işaretlenir ve **varsayılan hiçbir listede görünmez** (panel, export, mail). Görmek için:

- Panelde **"Arz ilanları (Fiverr)"** kutusunu işaretle,
- ya da `py -m scanner export --include-supply`.

Bu ayrım olmasa Fiverr gig'leri 48–52 puanla gerçek projelerin üstüne çıkıyordu; talep listesi
kirlenmesin diye ayrı tutuluyor.

## Puanlama

`config/keywords.yaml` — asıl iş `signals` bölümünde:

| Sinyal | Puan | Neden |
|---|---|---|
| `remote_boost` | **+12** | Ana hedef: uzaktan çalışılabilen proje |
| `contract_boost` | **+8** | Proje bazlı / freelance sözleşme |
| `hybrid_boost` | +5 | İkinci tercih |
| `fresh_boost` | +3 | Son 7 günde yayınlanmış |
| `onsite_penalty` | −4 | Yerinde çalışma |
| `permanent_penalty` | −3 | Kadrolu pozisyon |

Kelime tarafı: `must_any` (abap/sap yoksa elenir), `boost` (sap abap +8, abap +6, freelance +6, contract +5,
s/4hana, fiori, rap, cds…), `penalty` (intern, yeni mezun, "no remote", "relocation required").
Başlıkta geçen kelime 2× sayılır. Eşleşme kelime sınırıyla yapılır ("rap" → "raporlama" saymaz),
Türkçe karakter sadeleşir.

Çalışma şekli tespiti (`normalize.detect_work_mode`): önce hibrit ifadeleri aranır — "hybrid remote"
yazan ilan **hibrit** sayılır, yanlışlıkla remote'a yazılmaz. freelancermap'te yüzde varsa
(`%100 remote`) doğrudan ondan hesaplanır: ≥80 remote, ≥20 hibrit, altı yerinde.

Ağırlık değişince `py -m scanner rescore` yeter, yeniden tarama gerekmez.

## Panel

Satır görünümü, sol kenarda çalışma şekline göre renkli şerit (yeşil=uzaktan, sarı=hibrit).
Her satırda: skor, UZAKTAN/HİBRİT rozeti, remote yüzdesi, proje bazlı etiketi, başlık (linkli),
firma, konum, süre, başlangıç, bütçe, ilan tarihi, kaynak.
**Filtreler:** serbest arama, **hariç tutulacak kelimeler**, çalışma şekli (uzaktan / uzaktan+hibrit /
hibrit / yerinde / belirsiz), kaynak, ülke, **yayın tarihi** (24 saat – 30 gün), **tarih aralığı**
(iki tarih arası; ilan tarihine ya da sisteme düşme tarihine göre), durum (yeni / takipte /
başvuruldu), min skor, proje bazlı, bütçesi belli, kapananlar. Üstte tek tıkla hazır filtreler:
"Uzaktan + proje bazlı", "Son 3 gün", "Takip listem", "Bütçesi belli", "Kapanan ilanlar".

**Sıralama** (`storage.ORDERS`, beyaz liste — bilinmeyen değer skora düşer):

| Grup | Seçenekler |
|---|---|
| Uygunluk | skor (yüksekten/düşükten), **çalışma şekli önceliği** (uzaktan > hibrit > belirsiz > yerinde, proje bazlı üste) |
| Bütçe | **bütçe yüksekten / düşükten** — bütçesi bilinmeyen ilan iki yönde de sonda |
| Tarih | ilan tarihi (yeniden eskiye / eskiden yeniye), sisteme düşme sırası |
| Diğer | firma adı |

Her sıralama `fingerprint` ile biter: eşit değerlerde SQLite satır sırası kararsız, LIMIT/OFFSET
sayfalamasında aynı ilan iki sayfada çıkabiliyordu.

**Tarih aralığı** `COALESCE(posted_at, first_seen_at)` üzerinde çalışır; üst sınır *ertesi günün
başı* ile **hariç** karşılaştırılır (`T23:59:59` ile karşılaştırmak mikro saniyeli damgaları
eliyordu). Aralık doluysa "son N gün" seçimi yok sayılır — ikisi aynı ekseni filtreliyor.

**Bütçeye göre sıralama** iki türetilmiş kolona dayanır (`normalize.parse_budget`):
`budget_amount` (metinden çıkarılan tutar, aralıkta üst uç) ve `budget_daily` (karşılaştırılabilir
günlük USD tahmini: saat×8, gün×1, hafta/5, ay/22, yıl/260). Periyot yazmayan tutarlar 20 bin –
1 milyon aralığındaysa **yıllık maaş** sayılır (`$100k - $150k` en yaygın biçim ve periyot yazmıyor);
ihale (TED) toplamları hariç tutulur. Götürü fiyatlı işlerde `budget_daily` NULL kalır — saatlik
$110 ile götürü $5.000'i aynı kolonda sıralamak yanıltıcı olurdu. Kurlar `panel.currency_rates`
(yoksa `normalize.DEFAULT_RATES`). Kolonlar `upsert`te yazılır; eski veritabanı migration sonrası
bir kez `_backfill_budget()` ile doldurulur.

**Satır aksiyonları:** Takibe al / Takipten çıkar, Başvurdum, Gizle, **Artık Aktif Değil**
(elle kapatma — bkz. "Bilinen sınır: Jooble linkleri hiç doğrulanamıyor") — filtre ve
sayfa korunarak döner.

**Arayüz — "radar konsolu":** koyu tema esas (açık temaya kağıt tonlarıyla döner, `prefers-color-scheme`).
Tipografi: başlıklar *Bricolage Grotesque*, gövde *IBM Plex Sans*, sayılar/etiketler *IBM Plex Mono*
(tabular rakam). Renk anlam taşır: turkuaz = uzaktan, kehribar = hibrit, mor = proje bazlı,
mercan = kapandı. Sayfa öğeleri:

- **Üst konsol:** dönen radar süpürmesi, gezinme, canlı tarama göstergesi + "Şimdi tara".
- **Durum karoları:** uzaktan / hibrit / proje bazlı / son 24 saat / son 7 günde yayınlanan / kapandı —
  her biri karşılık gelen filtreye link, sayılar canlı tazelenir.
- **Hızlı filtre çipleri** + katlanır **Filtreler** paneli (varsayılan kapalı, dolu filtreyle açık).
- **İlan kartı:** SVG skor halkası (50 puan tam tur), rozetler, göreli tarih ("3 gün önce") ve tazelik
  noktası (yeşil ≤3 gün, sarı ≤14 gün, gri daha eski), ikonlu satır aksiyonları.
- **Bildirim şeridi:** yeni ilan düştüğünde alttan çıkan toast; sayfa kendiliğinden yenilenmez.

Erişilebilirlik: 4.5:1 üstü kontrast (koyu ve açık temada ölçüldü), 44px dokunma hedefleri
(`pointer: coarse`), görünür odak halkaları, `aria-current` / `aria-pressed`, `prefers-reduced-motion`.
Sayfa başına ilan: `panel.page_size` (20). Stil ve script `web/static/` altında ayrı dosyalarda.

## Ayarlar ekranı (`/ayarlar`)

Kaynak yönetimi artık dosya düzenlemeyi gerektirmiyor. Panelde dördüncü sekme:

Tasarım hedefi: **teslimden sonra kimse kod ya da YAML açmak zorunda kalmasın.**

| Bölüm | Ne düzenlenir |
|---|---|
| **Ne arıyoruz** | Arama sorguları, zorunlu kelimeler, kesin eleyenler, puan artıran/düşüren kelimeler, sinyaller, min skor — konuyu buradan değiştirirsiniz (SAP → ERP), kaydedince otomatik rescore |
| **Kaynaklar** | Her kaynağı aç/kapat, sayfa sayısı, arama sorguları ve kaynağa özel listeler (freelancermap anahtar kelimeleri, freelancer.com yetenek kodları, Careerjet dilleri, TED CPV kodları) |
| **API anahtarları** | Her değişken için maskeli durum + yeni değer / sil. Kayıtlı anahtar **hiçbir zaman** tarayıcıya basılmaz; boş bırakılan alan mevcut anahtarı değiştirmez |
| **Tarama** | Otomatik taramayı aç/kapat, tam ve hızlı tur süreleri, link kontrolü limitleri, sayfa başına ilan |
| **Yeni kaynak ekle** | Kod yazmadan RSS ya da JSON kaynağı tanıtma (aşağıda) |

### Ne arıyoruz — konuyu değiştirmek

`queries` (kaynaklara gönderilen arama metinleri) `config.yaml`'da, kelime ve ağırlıklar
`keywords.yaml`'da duruyordu; ikisi tek formda birleştirildi. Kaydetme sırası: önce
`queries` overlay'i, sonra keywords overlay'i, ardından `rescore_all()`.

İki incelik:

- **Sözlükler birleştirilmez, değiştirilir** (`settings.replace_map`). Konu SAP'tan ERP'ye
  dönünce formdan silinen kelimeler düz birleştirmede tabandan geri geliyordu; artık
  kaybolan her anahtara mezar taşı (`None`) yazılır.
- **İç içe değerler forma girmez.** `signals.country_boost` bir sözlük; `kelime: sayı`
  satırına sığmaz. `format_weight_lines` onu atlar, `replace_map` de silmez — yoksa
  ekranı açıp kaydetmek ayarı bozardı (test bunu sabitliyor).

`must_any` boş bırakılamaz: hiçbir ilan elenmez ve liste alakasız ilanla dolardı.

### Kodsuz yeni kaynak (`sources/custom.py`)

Ayarlar'daki form bir **RSS/Atom akışını** ya da **JSON API'sini** tanıtır; tanım
`config["sources"][ad]` altına yazılır ve `kind` alanı taşıdığı için `CustomSource` ile
çalışır (`sources.source_class()` adı REGISTRY'de bulamazsa buna düşer). Böylece kodsuz
kaynak, kodda tanımlı kaynaklarla **aynı** yoldan geçer: aç/kapat, sorgular, tarama,
dedupe, puanlama, kapanan tespiti.

```yaml
sources:
  weworkremotely:
    kind: rss                      # rss | json
    url: "https://site/feed?q={sorgu}&p={sayfa}"
    items_path: "data.results"     # (json) ilan listesinin yolu
    fields: {title: baslik, url: yol, company: "sirket.ad"}
```

- `{sorgu}` / `{sayfa}` yer tutucuları doldurulur. **`{sorgu}` yoksa akış bir kez çekilir**
  ve sonuç önbelleğe alınır — yoksa 4 sorgu aynı sayfayı 4 kez indiriyordu.
- Alan yolları noktalı (`sirket.ad`), liste içindeki öğeye `[0]` ile ulaşılır.
- RSS'te eşleme **zorunlu değil**: `title/link/description/pubDate/author` otomatik bulunur.
  Etiket adları büyük/küçük harf duyarsız aranır (`pubDate` ararken `pubdate` yazmak
  bulamıyordu) ve Atom karşılıkları (`published`, `summary`, `dc:creator`) sırayla denenir.
- JSON'da `title` ve `url` zorunlu; ikisi olmadan ilan işe yaramaz.

**Hata mesajları kullanıcıyı yönlendirir** (`MappingError` — ağ hatasından ayrı tutulur,
"kaynak yanıt vermedi" demek yerine formda neyin yanlış olduğu söylenir):

| Durum | Mesaj |
|---|---|
| Yanlış `items_path` | "Ilan listesi bulunamadi (yol: …)" |
| Tek nesneye işaret | "'…' bir ilan listesi degil. Buradaki alanlar: …" |
| RSS yerine HTML | "Adres RSS degil, HTML sayfa dondu" |
| Alan adı yanlış | "175 kayit bulundu ama hicbirinden baslik+link okunamadi. … Ornek kayittaki alanlar: slug, company_name, title…" |

Son satır kritik: kullanıcı hangi alan adlarını yazacağını **denemeden** bilemez.

**"Bu tanımı dene"** kaydetmeden sınar (`/api/kaynak-kontrol` gövdesinde `definition`);
çalışmayan bir tanımın önce kaydedilmesi gerekmez.

**"Veriyi kontrol et"** (`POST /api/kaynak-kontrol` → `probe.py`): kaynağı kayıtlı ayarlarla
canlı dener, kaç ilan geldiğini + ilk 3 başlığı + süreyi gösterir. **Veritabanına yazmaz** —
`probe.py` `Storage`'ı hiç import etmez, dry-run yapısal olarak garanti (test bunu doğruluyor).
Tek sorgu, tek sayfa, tek deneme, hız sınırı kapalı (tarama istemcisinin 3 deneme + `2**n`
backoff'u bir kontrolü 14+ saniyeye çıkarırdı). `BlockedError` Türkçeye çevrilir: 401 "anahtar
reddedildi", 403 "bot koruması", 429 "çok fazla istek"; anahtar eksikse **ağa çıkılmadan** söylenir.

Kısaltma sadece **her elemanı ayrı istek olan** listelere uygulanır (`probe_trim`): sorgular,
freelancermap anahtar kelimeleri, Careerjet dilleri. Tek isteğe sığan kod
listeleri (`skill_ids`, `cpv`) kısaltılmaz — aramayı daraltıp yanlış "sonuç yok" üretirdi.

### Ayarlar nereye yazılır

`config.yaml` **asla yeniden yazılmaz**: içindeki yorumlar her kaynağın neden öyle ayarlandığını
belgeliyor, PyYAML round-trip'i hepsini silerdi. Bunun yerine overlay dosyaları yükleme sırasında
taban üzerine derin birleştirilir:

| Dosya | İçerik |
|---|---|
| `config/settings.local.yaml` | Kaynak/tarama/panel ayarları (`config.yaml` üzerine) |
| `config/keywords.local.yaml` | Puanlama ağırlıkları (`keywords.yaml` üzerine) |
| `.env` | API anahtarları (git'e girmez) |

Üçü de `paths.data_dir()` altında — geliştirmede ve sunucuda proje klasörü, kurulu exe'de
`%LOCALAPPDATA%`. **Dosyalar proje klasörüyle birlikte taşınır**, sunucuya kopyalamak yeterli.

Birleştirme kuralı (`config.deep_merge`): dict+dict özyineli birleşir, `None` anahtarı **siler**
(mezar taşı — kullanıcı bir kelimeyi listeden çıkardığında saf birleştirme onu tabandan geri
getirirdi), liste ve skalerler tamamen değiştirilir. `load_config()` cache'siz olduğu için
kaynak/sorgu değişikliği yeniden başlatmadan geçerli olur.

Kaydederken `config.prune_unchanged()` tabanla **aynı** olan değerleri eler — overlay yalnızca
kullanıcının gerçekten değiştirdiğini tutar. Bu olmasa panel her kaydedişte bütün listeleri
kopyalar, `config.yaml`'daki varsayılanlar donar ve sonraki sürümde iyileştirilen sorgu listesi
kullanıcıya hiç ulaşmazdı.

**Anahtarlar:** `save_env()` `.env`'in satır sırasını ve yorumlarını korur, ardından
`_load_dotenv(force=True)` ile çalışan sürecin ortamını tazeler — normal yükleme
`os.environ.setdefault` kullandığı için değişen anahtar aksi halde ancak yeniden başlatınca
görülürdü. Upwork uygulama bilgisi değişince `data/upwork_token.json` silinir, yoksa eski token
kullanılıp kontrol yanıltıcı "başarılı" verirdi.

**Tarama ayarları anında geçerli:** `BackgroundScanner.reconfigure()` aralıkları kilit altında
günceller ve uykuyu böler. Aç/kapat thread'i durdurmaz, **duraklatır** (`set_enabled`) — masaüstü
uygulaması zamanlayıcıyı kendi yönetiyor (`external_scheduler`), panel onu durdurmamalı.
"Şimdi tara" duraklatılmışken de çalışır: düğmeye basan kullanıcı taramayı istiyordur.

**Sızıntı önlemi:** Jooble anahtarı URL yolunda gidiyor ve hata mesajı URL'yi içeriyordu; bu mesaj
`runs` tablosuna ve panele düşüyordu. `settings.scrub()` bilinen anahtar değerlerini `***` yapar,
hem kontrol çıktısına hem pipeline'ın hata kaydına uygulanır.

**Yan düzeltme:** `requires_env` tek değişken tutuyordu; bazı kaynaklar iki anahtar istiyor ve ilki
varken `APP_KEY` yoksa kaynak çalışıp 401 alıyordu. Artık `BaseSource.env_requirements()` tüm
zorunlu değişkenleri bildiriyor, `pipeline.missing_credentials()` hepsini kontrol ediyor.

## Masaüstü programı (patronun bilgisayarı)

Terminal bilmeyen kullanıcı için çift tıkla açılan pencere:

- **Durum şeridi:** yeşil nokta + faz ("Tam tarama: tüm kaynaklar…", "Çalışıyor"),
  sonraki tura geri sayım, son turda kaç yeni/güncel ilan.
- **Düğmeler:** Başlat/Durdur · Paneli Aç · Şimdi Tara. Excel export CLI'da kalır
  (`py -m scanner export`); masaüstü arayüzünden kaldırıldı, panel zaten asıl ekran.
- **Sayı kartları:** aktif ilan, uzaktan, proje bazlı, son 24 saat, kapandı (1 sn'de bir tazelenir).
- **Kayıtlar sekmesi:** canlı log akışı; **Ayarlar sekmesi:** Windows açılışında başlat,
  tarama aralıkları, hangi kaynağın anahtarı var, veri klasörünü aç.
- Pencere kapatılınca **tepsiye iner**, tarama devam eder. İlk başlatmada panel tarayıcıda açılır.

Çalıştırma: `py -m scanner gui` (geliştirme) ya da kurulu `SAP Proje Radari.exe`.

### Paketleme
```powershell
powershell -ExecutionPolicy Bypass -File .uild.ps1
```
1. `tools/embed_keys.py` → `.env`'deki API anahtarlarını `_embedded_env.py` olarak pakete gömer
   (git'e girmez; kurulu programda `.env` bulunmadığı için anahtarlar buradan okunur).
2. Testler çalışır — başarısızsa derleme durur.
3. PyInstaller **onedir** + `--windowed` → `dist\SAP Proje Radari\`
4. Inno Setup → `dist\SAPProjeRadari-Setup.exe` (Başlat menüsü, masaüstü kısayolu,
   "Windows açılışında başlat" seçeneği, Program Ekle/Kaldır kaydı, kaldırırken veriyi sorar).

**Yollar:** kurulu programda okunacak dosyalar paketten (`paths.bundle_dir()`), yazılacak her şey
`%LOCALAPPDATA%\SAP Proje Radari` altına gider (`paths.data_dir()`) — Program Files yazılabilir değil.

⚠️ Kod imzalama sertifikası yok: ilk çalıştırmada SmartScreen "Bilinmeyen yayımcı" uyarısı çıkar
("Daha fazla bilgi" → "Yine de çalıştır").
⚠️ Gömülü anahtarlar exe içinden teknik olarak çıkarılabilir; anahtar değişirse yeniden derleme gerekir.

**2026-08-19 düzeltmesi — "Başlat" düğmesi sessizce hiçbir şey yapmıyordu:** `--windowed`
pakette konsol olmadığı için arka plan iş parçacığındaki (worker thread) yakalanmamış
istisnalar hiçbir yere yazılmadan kayboluyordu; kullanıcı düğmeye basıyor, pencere
"Durduruldu" yazısında donup kalıyordu. `start_service`/`stop_service` artık her hatayı
yakalayıp hem log dosyasına yazıyor hem de bir hata penceresi gösteriyor.

**Aynı tarih — panelin üst bardaki canlı durumu masaüstü uygulamasında hep "kapalı" görünüyordu:**
`RadarService._start_panel()` paneli `auto_scan=False` ile açıyordu (çift zamanlayıcı
önlemek için), ama bu panelin kendi durumu sorması gereken zamanlayıcıyı hiç göstermemesi
anlamına geliyordu. `create_app()` artık `external_scheduler` parametresi kabul ediyor;
`RadarService` kendi `BackgroundScanner`'ını önce kurup panele geçiriyor, panel de onun
üzerinden okuyup gösteriyor — kendi zamanlayıcısını başlatıp durdurmuyor.

## Mimari

```
config/          config.yaml, keywords.yaml (taban)
                 settings.local.yaml, keywords.local.yaml (panelden yazılan overlay)
src/scanner/
  cli.py         scan / export / mail / rescore / serve / watch / stats
  scheduler.py   arka plan tarama döngüsü (panel ve `watch` bunu kullanır)
  pipeline.py    kaynaklar -> dedupe -> puanlama -> DB; rescore_all()
  settings.py    kaynak katalogu, anahtar maskeleme/temizleme, form ayrıştırma
  probe.py       tek kaynağı canlı deneme ("veriyi kontrol et"); DB'ye dokunmaz
  paths.py       veri/paket klasörleri (kurulu exe %LOCALAPPDATA%'ya yazar)
  desktop/       launcher.py (Tkinter pencere + tepsi ikonu), service.py (ajan + panel)
  sources/       base.py (HTTP + hız sınırı + retry + BlockedError),
                 jooble.py, freelancermap.py, freelancercom.py, arbeitnow.py, kariyernet.py,
                 upwork.py + upwork_auth.py (OAuth2 token deposu ve otomatik yenileme),
                 reed.py, careerjet.py, ted.py,
                 custom.py (panelden eklenen RSS/JSON kaynaklari)
  models.py      Project (work_mode, remote_percent, is_contract, duration, budget…)
  normalize.py   HTML temizleme, Türkçe fold, tarih, remote/hibrit ve sözleşme tespiti
  dedupe.py      fingerprint = başlık + firma (gürültü kelimeleri atılarak)
  scoring.py     kelime puanı + çalışma şekli/sözleşme sinyalleri
  storage.py     SQLite (projects, runs, notifications) + sayfalama, migration
  export.py      Excel/CSV
  mailer.py      SMTP + HTML rapor
  web/           FastAPI panel: templates/ (base + index + notifications + settings),
                 static/app.css (tasarım sistemi), static/app.js (canlı durum, toast),
                 static/settings.js (yalnızca "veriyi kontrol et")
tests/           144 test, ağa çıkmaz
run_agent.ps1    panelden bağımsız sürekli ajan
install_agent.ps1 ajanı Windows oturum açılışına bağlar
```

Tasarım kararları:
- **Kaynak izolasyonu:** biri patlarsa tarama durmaz, hata `runs` tablosuna yazılır.
- **Engellenme (403/429) = `BlockedError`:** tekrar denenmez, o kaynağın kalan sorguları atlanır.
- **Kaynak başına sorgu listesi:** `sources.<ad>.queries`.
- **Aynı ilan iki kaynakta:** tek kayıt; eksik alanlar (remote yüzdesi, süre, bütçe) diğerinden tamamlanır.
- **Puan kaybını önleme:** kaynak açıklama vermezse DB'deki açıklama geri yüklenir.
- **Şema göçü:** yeni kolonlar `MIGRATIONS` ile eklenir, indeksler migration'dan sonra kurulur —
  eski veritabanı silinmeden çalışmaya devam eder.

## Test

```bash
py -m pytest -q
```

## Durum (2026-08-17 son tarama)

1081 ham ilan → **268 aktif proje**: **131 uzaktan**, 47 hibrit, **192 proje bazlı**.
64 yeni ilan bildirimi düştü. En yüksek skorlu: *Senior SAP ABAP Developer – S/4HANA (Remote)*,
$100–110/saat (Jooble).

Jooble sorgu listesi SAP modüllerine (MM, SD, FICO) genişletildi, freelancermap'te 16 anahtar
kelime sayfası taranıyor (sap-mm, sap-sd, sap-fico, sap-ewm, sap-bw, sap-basis …).

## Yol haritası

- **Faz 1 (bitti):** 5 kaynak, remote öncelikli puanlama, sayfalanan panel, Excel/CSV.
- **Faz 2 (kısmen):** güncellik takibi + bildirim merkezi + genişletilmiş filtreler bitti.
  Kalan: `run_daily.ps1`'i Windows Task Scheduler'a kaydetmek ve günlük mail'i açmak.
- **Faz 3:** LLM ile ilan özeti ve "bu proje neden bize uygun"
  gerekçesi, başvuru takibi raporu.
