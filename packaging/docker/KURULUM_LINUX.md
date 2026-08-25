# SAP Proje Radarı — Linux kurulumu (Docker)

Bu klasörde çalışan bir uygulamanın **tamamı** var: program, ayarlar ve API
anahtarları imajın içinde. Hiçbir şey doldurmanız gerekmiyor.

Gereken tek şey Docker. Ubuntu'da yoksa:

```bash
sudo apt update && sudo apt install -y docker.io && sudo usermod -aG docker $USER
```

Son komuttan sonra **oturumu kapatıp açın** (yoksa script her seferinde `sudo` sorar).

---

## Kurulum — tek satır

Klasörü açtığınız yerde:

```bash
chmod +x *.sh && ./Radar_Baslat.sh
```

İlk çalıştırma imajı yüklediği için ~1 dakika sürer. Bittiğinde adres yazılır:

```
Hazir:  http://127.0.0.1:8000
```

Masaüstü varsa tarayıcı kendiliğinden açılır ve **"SAP Proje Radarı"** kısayolu
uygulama menüsüne eklenir — bundan sonrası tek tıklama.

Sunucuda (SSH ile) masaüstü yoktur; adresi kendiniz açarsınız. Uzaktan bağlanmak
için ya SSH tüneli:

```bash
ssh -L 8000:127.0.0.1:8000 kullanici@sunucu
```

ya da paneli ağa açın (**önce parolayı değiştirin**):

```bash
RADAR_BIND=0.0.0.0 ./Radar_Baslat.sh
```

---

## İlk giriş

Kullanıcı **admin**, parola **123456**. İlk iş: Ayarlar → Hesabım'dan değiştirin.

---

## Günlük kullanım

| İş | Komut |
|---|---|
| Başlat / paneli aç | `./Radar_Baslat.sh` |
| Durdur | `./Radar_Durdur.sh` |
| Ne yapıyor, canlı izle | `./Radar_Gunluk.sh` (çıkış: Ctrl+C) |
| Yeni sürüm kur | `./Radar_Guncelle.sh` (yeni `.tar` dosyasını klasöre koyup) |

Makine yeniden başlayınca panel kendiliğinden kalkar (`--restart unless-stopped`).

---

## Ayarlar

`ayarlar.conf` dosyasından kalıcı, ortam değişkeniyle tek seferlik:

```bash
RADAR_PORT=8010 ./Radar_Baslat.sh      # 8000 doluysa
```

---

## Veri nerede

Her şey `sap-radar-veri` adlı Docker biriminde: veritabanı, ayarlar, Excel
çıktıları, günlükler. Konteyneri silmek veya güncellemek veriyi silmez.

Yedek:

```bash
docker run --rm -v sap-radar-veri:/veri -v "$(pwd):/yedek" alpine tar czf /yedek/radar-yedek.tar.gz -C /veri .
```

Geri yükleme:

```bash
docker run --rm -v sap-radar-veri:/veri -v "$(pwd):/yedek:ro" alpine tar xzf /yedek/radar-yedek.tar.gz -C /veri
```

> Veriyi gerçekten silen tek komut: `docker volume rm sap-radar-veri`.

---

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `permission denied ... docker.sock` | `sudo usermod -aG docker $USER`, oturumu yenileyin (script bu arada `sudo` ile devam eder) |
| `port is already allocated` | `RADAR_PORT=8010 ./Radar_Baslat.sh` |
| Panel açılıyor, ilan yok | `./Radar_Gunluk.sh` — "atlandi" satırı hangi kaynağın atlandığını yazar |
| `Cannot connect to the Docker daemon` | `sudo systemctl start docker` |
| Script çalışmıyor: `Permission denied` | `chmod +x *.sh` |

---

## Güvenlik notu

API anahtarları imajın içinde. `.tar` dosyasını veya imajı alan herkes
`docker run --rm sap-proje-radari:SÜRÜM cat /app/.env` ile okuyabilir. Paketi
güvenmediğiniz kimseye vermeyin; sızarsa anahtarları ilgili sitelerden yenileyin.
