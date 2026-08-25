# syntax=docker/dockerfile:1
#
# SAP Proje Radari - tek konteyner.
# Panel ve arka plan taramasi AYNI surecte calisir (scheduler panelin icinde
# donuyor), bu yuzden ayri bir "worker" servisine gerek yok - ve tam tersine,
# birden fazla replika calistirmak AYNI SQLite dosyasina iki tarama yazar.
# Bkz. DOCKER.md > "Tek replika kurali".

FROM python:3.14-slim

# RADAR_DATA_DIR: uygulamanin YAZDIGI her sey buraya duser -
# veritabani, .env, ayar overlay'leri, Excel ciktilari, gunlukler.
# Tek birim baglamak bu yuzden yeterli.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    RADAR_DATA_DIR=/veri \
    TZ=Europe/Istanbul

WORKDIR /app

# Bagimliliklar once kopyalanir: kod degistiginde bu katman onbellekten gelir.
COPY requirements.txt ./
# pystray/pillow masaustu tepsi ikonu icin, pytest test icin - sunucuda hicbiri
# calismiyor. requirements.txt tek dogru kaynak kalsin diye ikinci bir dosya
# tutmak yerine burada suzuluyor.
RUN grep -viE '^(pystray|pillow|pytest)' requirements.txt > /tmp/req-sunucu.txt \
 && pip install --no-cache-dir -r /tmp/req-sunucu.txt

# Salt okunur uygulama katmani: kod + TABAN ayarlar.
# Kullanicinin panelden degistirdikleri config/*.local.yaml olarak /veri altina
# yazilir, buradaki dosyalar hic degismez.
COPY src/ ./src/
COPY config/ ./config/

# ANAHTARLARIN IMAJA GOMULMESI (istege bagli).
#
# Tek .tar dosyasi olarak teslim edilen imajda .env yoktur; alici hicbir sey
# girmeden calistirabilsin diye anahtarlar imajin icine konur.
# Dosyayi tools/paket_docker.ps1 uretir: .env -> .env.paket (derleme bitince siler).
# .env'in kendisi .dockerignore ile disarida; ayri ad bilincli tercih -
# "anahtarli imaj" ancak paketleme script'i ile bilerek uretilir.
#
# requirements.txt bu COPY'de yalnizca joker sifir eslesmede hata vermesin diye
# duruyor (BuildKit "no source files" der); ikinci kopya zararsiz.
#
# /app/.env, config.py `_load_dotenv` icindeki ROOT/.env yolu. Okuma sirasi:
#   1) /veri/.env       panelden girilen  (alicinin girdigi kazanir)
#   2) /app/.env        gomulu anahtarlar <- buraya yaziliyor
#   3) _embedded_env.py imajda YOK
# setdefault ile okundugu icin gercek ortam degiskeni (-e JOOBLE_API_KEY=...)
# ikisinin de onunde kalir.
#
# DIKKAT: bu imaji alan herkes `docker run --rm IMAJ cat /app/.env` ile
# anahtarlari okuyabilir. Tar dosyasini paylasirken bunu hesaba katin.
COPY requirements.txt .env.paket* ./
RUN if [ -f .env.paket ]; then mv .env.paket .env; fi

# Kok kullanici olarak calistirma. /veri baglanan birimin baglama noktasi.
RUN useradd --system --uid 10001 --create-home radar \
 && mkdir -p /veri \
 && chown -R radar:radar /veri /app
USER radar

EXPOSE 8000

# Panel ayaga kalkti mi: /giris oturum istemeden 200 doner.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/giris', timeout=4).status_code == 200 else 1)"

# --host 0.0.0.0 ZORUNLU: varsayilan 127.0.0.1 konteyner disindan erisilemez.
CMD ["python", "-m", "scanner", "serve", "--host", "0.0.0.0", "--port", "8000"]
