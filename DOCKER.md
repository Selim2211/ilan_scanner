# Docker ile Çalıştırma

Bu belgedeki her komut bu makinede çalıştırılıp doğrulandı (Docker 29.7.2, Docker Desktop).
İmaj derlendi, konteyner ayağa kalktı, panel yanıt verdi, tarama yaptı.

---

## 0. Neden tek birim (volume) yetiyor

Konteynerleştirmenin en zor kısmı genelde "hangi klasörleri kalıcı yapacağız" sorusudur.
Bu projede cevap zaten kodda hazır: `paths.py` **okunacak** ve **yazılacak** kökleri ayırıyor
ve yazma kökü `RADAR_DATA_DIR` ortam değişkeniyle taşınabiliyor.

| | Nerede | Konteynerde |
|---|---|---|
| **Okunur** — kod, taban `config/*.yaml`, şablonlar | `bundle_dir()` | imajın içi, `/app` |
| **Yazılır** — veritabanı, `.env`, ayar overlay'leri, Excel çıktıları, günlükler | `data_dir()` | birim, `/veri` |

Doğrulandı — `RADAR_DATA_DIR=/veri` verildiğinde uygulamanın yazdığı **her şey** oraya düşüyor:

```
/veri/data/projects.db          veritabanı
/veri/.env                      panelden girilen anahtarlar
/veri/config/settings.local.yaml    panelden değişen ayarlar
/veri/config/keywords.local.yaml    panelden değişen kelimeler
/veri/output/                   Excel çıktıları
/veri/output/logs/              günlükler
```

Yani **tek `-v` yeterli.** İkinci bir birim, ayrı bir config mount'u gerekmiyor.

---

## 1. Dosyalar

Üç dosya proje köküne eklendi:

| Dosya | Görevi |
|---|---|
| `Dockerfile` | İmaj tarifi — Python 3.14-slim tabanlı, kök olmayan kullanıcı, sağlık kontrolü |
| `.dockerignore` | İmaja **girmeyecekler** — en önemlisi anahtarlar |
| `docker-compose.yml` | Çalıştırma tarifi — port, birim, anahtarlar, yeniden başlatma |

### 1.1 `.dockerignore` — iki kritik satır

```
.env
src/scanner/_embedded_env.py
```

Birincisi bariz. **İkincisi bu projeye özgü bir tuzak:** `_embedded_env.py`, kurulu `.exe`
sürümü için `tools/embed_keys.py` tarafından üretilen ve **gerçek API anahtarlarını içeren**
bir dosya. `src/` altında durduğu için `COPY src/` ile imaja sızıyor.

Derleme sırasında bu yakalandı — anahtar imajın içindeydi ve `.env` geçirilmediği halde
konteyner Jooble'a istek atıyordu. Hariç tutulduktan sonra:

```
_embedded_env.py: imajda YOK
jooble atlandi: JOOBLE_API_KEY tanimli degil     ← anahtar artık dışarıdan geliyor
```

`config.py` bu modülü `try/except ImportError` ile okuduğu için dosyanın olmaması sorun
çıkarmıyor; anahtarlar ortam değişkeninden gelir.

### 1.2 `Dockerfile` — üç önemli karar

```dockerfile
ENV RADAR_DATA_DIR=/veri          # yazılan her şey tek yere
CMD [..., "serve", "--host", "0.0.0.0", ...]
USER radar                        # kök değil
```

- **`--host 0.0.0.0` zorunlu.** `serve` komutunun varsayılanı `127.0.0.1` — konteyner
  içinde bu "yalnızca konteynerin kendisi erişebilir" demektir, port yayınlansa bile
  dışarıdan bağlanamazsınız.
- **Masaüstü bağımlılıkları kurulmuyor.** `pystray`, `pillow` (tepsi ikonu) ve `pytest`
  sunucuda çalışmıyor; `requirements.txt` tek doğru kaynak kalsın diye ikinci bir dosya
  tutmak yerine derleme sırasında süzülüyorlar.
- **Sağlık kontrolü** `/giris` adresine bakar (oturum istemeden 200 döner).

---

## 2. Anahtarlar

`docker-compose.yml` host'taki `.env` dosyasını okuyup içeriğini ortam değişkeni olarak
geçiriyor:

```yaml
env_file:
  - .env
```

Bu güvenli, çünkü uygulama **gerçek ortam değişkenlerine öncelik veriyor**
(`config.py` → `_load_dotenv(force=False)`). Birimdeki `/veri/.env` dosyası bunları ezemez.

Kurumsal bir sır yöneticisi kullanacaksanız `env_file` yerine:

```yaml
environment:
  JOOBLE_API_KEY:    ${JOOBLE_API_KEY:?anahtar tanımlı değil}
  JOOBLE_API_KEY_DE: ${JOOBLE_API_KEY_DE:?anahtar tanımlı değil}
  JOOBLE_API_KEY_UK: ${JOOBLE_API_KEY_UK:?anahtar tanımlı değil}
  REED_API_KEY:      ${REED_API_KEY:-}
  CAREERJET_AFFID:   ${CAREERJET_AFFID:-}
```

Anahtarı olmayan kaynak hata vermez, sessizce atlanır — panel yine çalışır.

---

## 3. Mevcut veriyi taşıma

Bu adım atlanırsa konteyner **boş bir veritabanıyla** başlar: 1.900 ilan, işaretleriniz,
arama profilleriniz ve kullanıcı hesaplarınız yeni kurulumda olmaz.

Önce panel/tepsi uygulamasını kapatın (SQLite yazarken kopyalamayın), sonra:

```bash
docker volume create sap-radar-veri
```

```bash
docker run --rm -v sap-radar-veri:/veri -v "$(pwd):/kaynak:ro" alpine sh -c "mkdir -p /veri/data /veri/config && cp /kaynak/data/projects.db /veri/data/ && cp /kaynak/config/*.local.yaml /veri/config/ 2>/dev/null; ls -la /veri/data"
```

PowerShell kullanıyorsanız `$(pwd)` yerine `${PWD}` yazın.

---

## 4. Çalıştırma

```bash
docker compose up -d --build
```

Port 8000 doluysa (tepsi uygulaması orada çalışıyor olabilir):

```bash
RADAR_PORT=8010 docker compose up -d --build
```

Doğrulama — üçü de yeşilse kurulum tamam:

```bash
docker compose ps
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/giris
```

```bash
docker compose logs --tail 20
```

Beklenen çıktı:

```
sap-radar · Up 22 seconds (healthy)
200
sap-radar  | jooble <- 'SAP ABAP remote': 100 ilan
```

Panel: **http://127.0.0.1:8000**

---

## 5. Günlük işler

| İş | Komut |
|---|---|
| Günlükleri izle | `docker compose logs -f` |
| Yeniden başlat | `docker compose restart` |
| Durdur | `docker compose down` (birim ve veri durur) |
| Kod değişince güncelle | `docker compose up -d --build` |
| Konteyner içinde komut | `docker compose exec radar python -m scanner stats` |
| Elle tarama | `docker compose exec radar python -m scanner scan` |
| Excel çıktısı al | `docker compose exec radar python -m scanner export --remote --contract` |

### Yedekleme

Tüm veri tek birimde, tek komutla yedeklenir:

```bash
docker run --rm -v sap-radar-veri:/veri -v "$(pwd):/yedek" alpine tar czf /yedek/radar-yedek.tar.gz -C /veri .
```

Geri yükleme:

```bash
docker run --rm -v sap-radar-veri:/veri -v "$(pwd):/yedek:ro" alpine tar xzf /yedek/radar-yedek.tar.gz -C /veri
```

---

## 5.5 Linux'a teslim: tek dosya paketi

Yukarıdaki bölümler *bu* makinede geliştirme içindir. Karşı tarafa (Ubuntu LTS sunucu)
verilecek paket ayrı üretilir: **tek zip**, içinde imaj + başlatıcılar, alıcı hiçbir
anahtar doldurmaz.

### Paketi üretmek (bu makinede)

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\paket_docker.ps1
```

Script sırayla: `.env`i `.env.paket` olarak kopyalar → imajı derler → kopyayı **siler** →
imajın içinden anahtarları gerçekten okuyabildiğini doğrular → `docker save` ile tar alır →
`packaging/docker/` altındaki script'leri ve `ayarlar.conf`u ekleyip zipler.

Sonuç: `dist\sap-radar-docker-1.8.0.zip` (~55 MB) — gönderilecek tek dosya.

| Bayrak | Etki |
|---|---|
| `-AnahtarsIz` | Anahtar gömülmez; alıcı panelden kendi anahtarlarını girer |
| `-Port 8010` | Alıcıdaki varsayılan port (`ayarlar.conf`a yazılır) |
| `-ZipYok` | Sıkıştırmaz, `dist\sap-radar-docker\` klasörü bırakır |

### Alıcının yaptığı

```bash
unzip sap-radar-docker-1.8.0.zip -d sap-radar
cd sap-radar && chmod +x *.sh && ./Radar_Baslat.sh
```

`Radar_Baslat.sh`: docker var mı bakar → imaj yoksa tar'dan yükler → konteyneri kurar →
sağlık kontrolü yeşillenene kadar bekler → adresi yazar. Masaüstü varsa tarayıcıyı açar ve
uygulama menüsüne kısayol ekler (`SAP-Radar.desktop`), yani sonraki açılışlar **tek tık** —
Windows'taki `Ilan_Tarayici_Baslat.bat` ile aynı deneyim. Başsız sunucuda tarayıcı denenmez,
sadece adres yazılır.

Yanındaki diğer script'ler: `Radar_Durdur.sh`, `Radar_Gunluk.sh` (canlı log),
`Radar_Guncelle.sh` (yeni tar'ı yükler, konteyneri yeniler; **veri ve port korunur**).

`ayarlar.conf` port/bind/imaj adını tutar. Ortam değişkeni onu ezer:
`RADAR_PORT=8010 ./Radar_Baslat.sh`.

### Anahtarlar imajın içinde — bilinçli ödünleşme

`Dockerfile`daki `COPY requirements.txt .env.paket* ./` satırı `.env.paket` varsa onu
`/app/.env` yapar. `config.py > _load_dotenv` okuma sırası:

| Sıra | Kaynak | Not |
|---|---|---|
| 1 | `/veri/.env` | Alıcının panelden girdiği — gömülüyü ezer |
| 2 | `/app/.env` | **Gömülü anahtarlar** |
| 3 | `_embedded_env.py` | İmajda yok (`.dockerignore`) |

`setdefault` ile okunduğu için gerçek ortam değişkeni (`-e JOOBLE_API_KEY=...`) hepsinin
önündedir.

> **Bedeli:** imajı alan herkes `docker run --rm sap-proje-radari:1.8.0 cat /app/.env` ile
> anahtarları okur ve bu geri alınamaz — tar kopyalandıysa kopyada da vardır. Anahtar
> sızarsa Jooble/Reed panelinden yenilenmesi gerekir. Anahtarsız teslim için
> `-AnahtarsIz` ile üretip alıcıya panelden girdirin.

`docker compose` ile yerel çalıştırma bundan etkilenmez: orada `.env.paket` üretilmediği
için imaj anahtarsız kalır, anahtarlar `env_file` ile dışarıdan geçer.

---

## 6. Dikkat edilecekler

**Tek replika kuralı.** Tarama zamanlayıcısı panelin *içinde* dönüyor ve veritabanı SQLite.
İki replika = aynı dosyaya iki tarama + iki katı API isteği. `docker-compose.yml` içindeki
`replicas: 1` bilinçli; artırmayın.

**Tepsi uygulaması ile aynı anda çalıştırmayın.** İkisi de aynı API anahtarlarını kullanır;
paralel çalışırlarsa Jooble kotası iki kat hızlı tükenir ve iki ayrı veritabanı oluşur.
Konteynere geçtiyseniz tepsi uygulamasını kapatın.

**İlk yönetici parolası.** İlk açılışta `admin` / `123456` hesabı oluşuyor. Konteyneri ağa
açmadan **önce** Ayarlar → Hesabım'dan değiştirin.

**Ağa açmak.** Varsayılan yalnızca bu makineden erişilebilir. Ağa açmak için:

```bash
RADAR_BIND=0.0.0.0 docker compose up -d
```

**Saat dilimi.** `TZ=Europe/Istanbul` ayarlı. Tarihler veritabanında UTC tutuluyor;
bu ayar yalnızca günlük dosyası adlarını ve konteyner saatini etkiler.

**İmaj boyutu.** 238 MB (python:3.14-slim tabanı ~130 MB). `lxml` hazır tekerlek geldiği
için derleyici gerekmiyor, çok aşamalı derlemeye gerek yok.

---

## 7. Sorun giderme

| Belirti | Sebep | Çözüm |
|---|---|---|
| `ports are not available: bind: ... yalnızca bir kullanıma izin veriliyor` | Port 8000 dolu (tepsi uygulaması) | `RADAR_PORT=8010 docker compose up -d` |
| Konteyner ayağa kalkıyor ama tarayıcıdan açılmıyor | `--host 127.0.0.1` ile başlatılmış | `Dockerfile` içindeki `CMD` satırında `--host 0.0.0.0` olmalı |
| `RuntimeError: Form data requires "python-multipart"` | Bağımlılık eksik | `requirements.txt` içinde `python-multipart` olmalı (eklendi) |
| Panel açılıyor ama ilan yok | Anahtarlar geçmemiş | `docker compose logs \| grep atlandi` — hangi anahtar eksik yazar |
| Veriler kayboldu | `docker compose down -v` çalıştırılmış (`-v` birimi siler) | Yedekten geri yükleyin; `-v` bayrağını kullanmayın |
| Değişiklik yansımıyor | İmaj yeniden derlenmemiş | `docker compose up -d --build` |

---

## Derleme sırasında yakalanan iki gerçek sorun

Konteynerleştirme, yerelde görünmeyen iki hatayı ortaya çıkardı:

1. **`python-multipart` bağımlılığı `requirements.txt`'te yoktu.** Panelin 12 rotası form
   gönderiyor; paket yerel makinede başka bir şeyle birlikte kurulu olduğu için eksiklik
   fark edilmemişti. Temiz kurulumda panel açılıyor ama **her form 500 veriyordu**.
   → `requirements.txt`'e eklendi.

2. **API anahtarı imaja gömülüyordu.** `src/scanner/_embedded_env.py` gerçek Jooble
   anahtarını taşıyor ve `COPY src/` ile imaja giriyordu. İmajı alan herkes anahtarı
   okuyabilirdi. → `.dockerignore`'a eklendi.

İkisi de yalnızca "temiz bir ortamda sıfırdan kur" adımı sayesinde görüldü.
