# SAP Proje Radarı

Uzaktan / proje bazlı SAP (ABAP, S/4HANA, Fiori) işlerini birden çok ilan
kaynağından toplayan, puanlayan ve tek panelde gösteren tarayıcı.

- **Kaynaklar:** Jooble, Reed, Careerjet, Arbeitnow, freelancermap, freelancer.com,
  kariyer.net, TED — anahtar isteyen kaynaklar anahtar yoksa sessizce atlanır.
- **Panel:** FastAPI + Jinja2; filtreleme, arama profilleri, puan dökümü,
  kullanıcı/yetki yönetimi, koyu/açık tema.
- **Tarama:** Panelin içinde dönen zamanlayıcı; tekilleştirme, puanlama, Excel çıktısı.
- **Dağıtım:** Windows'ta tepsi uygulaması / kurulum exe'si, Linux'ta Docker.

## Hızlı başlangıç (kaynaktan)

```bash
pip install -r requirements.txt
cp .env.example .env      # anahtarları doldurun (hepsi isteğe bağlı)
PYTHONPATH=src python -m scanner serve
```

Panel: http://127.0.0.1:8000 — ilk giriş `admin` / `123456`.

Diğer komutlar:

```bash
PYTHONPATH=src python -m scanner scan      # tek seferlik tarama
PYTHONPATH=src python -m scanner stats     # veritabanı özeti
PYTHONPATH=src python -m scanner export --remote --contract
```

## Docker

```bash
docker compose up -d
```

Ayrıntı ve tek dosyalık Linux teslim paketi: [DOCKER.md](DOCKER.md).

## Belgeler

| Dosya | İçerik |
|---|---|
| [KURULUM.md](KURULUM.md) | Windows kurulumu, tepsi uygulaması |
| [DOCKER.md](DOCKER.md) | Konteyner, birim, teslim paketi |
| [MIMARI.md](MIMARI.md) | Modüller, veri akışı, şema |
| [PROJECT.md](PROJECT.md) | Kararlar ve geliştirme günlüğü |

## Testler

```bash
PYTHONPATH=src python -m pytest -q
```

## Yapılandırma

`config/config.yaml` taban ayarlar, `config/keywords.yaml` puanlama kelimeleri.
Panelden yapılan değişiklikler bunları ezmez; `*.local.yaml` overlay olarak yazılır.
API anahtarları `.env` dosyasından veya ortam değişkeninden okunur.
