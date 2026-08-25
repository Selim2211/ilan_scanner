"""Derleme oncesi calisir: .env icindeki anahtarlari pakete gomulecek modul haline getirir.

Patronun makinesinde .env dosyasi olmayacak; program anahtarlari bu modulden okur
(bkz. config.py -> _load_dotenv). Uretilen dosya git'e girmez.

    py tools/embed_keys.py            # .env -> src/scanner/_embedded_env.py
    py tools/embed_keys.py --clear    # gomulu anahtarlari temizle
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "scanner" / "_embedded_env.py"
sys.path.insert(0, str(ROOT / "src"))


def _embed_keys() -> list[str]:
    """Gomulecek anahtarlar kaynak bildirimlerinden turetilir.

    Elle yazili liste tutulunca yeni bir kaynak anahtari sessizce gomulmeden
    kaliyordu. SMTP parolasi gibi kisisel degerler zaten burada yok - sadece
    kaynaklarin `env_vars`/`requires_env` bildirimleri toplaniyor.
    """
    from scanner.sources import REGISTRY

    keys: list[str] = []
    for cls in REGISTRY.values():
        for var in cls.env_requirements():
            if var.name not in keys:
                keys.append(var.name)
    return keys


EMBED_KEYS = _embed_keys()

HEADER = '''"""Pakete gomulu API anahtarlari - `tools/embed_keys.py` uretir, elle duzenlemeyin.

Bu dosya git'e girmez. Kurulu programda .env bulunmadigi icin anahtarlar
buradan okunur; kullanicinin makinesindeki .env varsa o oncelikli olur.
"""

EMBEDDED_ENV = {
'''


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_module(values: dict[str, str]) -> None:
    lines = [HEADER]
    for key in EMBED_KEYS:
        value = values.get(key, "")
        lines.append(f'    {key!r}: {value!r},\n')
    lines.append("}\n")
    TARGET.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    if "--clear" in argv:
        write_module({})
        print(f"gomulu anahtarlar temizlendi -> {TARGET}")
        return 0

    values = read_env(ROOT / ".env")
    dolu = [k for k in EMBED_KEYS if values.get(k)]
    bos = [k for k in EMBED_KEYS if not values.get(k)]
    write_module(values)

    print(f"gomulen anahtar ({len(dolu)}): {', '.join(dolu) or '-'}")
    if bos:
        print(f"bos birakilan  ({len(bos)}): {', '.join(bos)}")
        print("  -> bu kaynaklar patronun makinesinde sessizce atlanir")
    print(f"yazildi: {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
