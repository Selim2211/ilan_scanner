"""Klasoru Linux'ta duzgun acilan bir zip'e koyar.

    py tools\\paket_zip.py <kaynak_klasor> <hedef.zip>

Neden ayri bir arac: PowerShell'in `Compress-Archive`'i alt klasor yollarini
TERS BOLU ile yaziyor ("src\\scanner\\cli.py"). Windows araclari bunu affediyor
ama Linux'un `unzip`i ters boluyu klasor ayraci saymiyor - dosyalar adinda
ters bolu olan tek bir yigin halinde aciliyor, `cd src` calismiyor.
Zip standardi ayrac olarak DUZ bolu ister; `zipfile` onu yaziyor.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# Pakete girmeyecekler - derleme artiklari ve onbellek.
ATLA_KLASOR = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
ATLA_UZANTI = {".pyc", ".pyo"}


def paketle(kaynak: Path, hedef: Path) -> int:
    sayi = 0
    hedef.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(hedef, "w", zipfile.ZIP_DEFLATED) as zip_dosyasi:
        for yol in sorted(kaynak.rglob("*")):
            if any(parca in ATLA_KLASOR for parca in yol.parts):
                continue
            if yol.suffix in ATLA_UZANTI or not yol.is_file():
                continue
            # as_posix(): ayrac her zaman duz bolu
            ic_ad = yol.relative_to(kaynak).as_posix()
            zip_dosyasi.write(yol, ic_ad)
            sayi += 1
    return sayi


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    kaynak, hedef = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    if not kaynak.is_dir():
        print(f"kaynak klasor yok: {kaynak}", file=sys.stderr)
        return 1

    sayi = paketle(kaynak, hedef)
    boyut = hedef.stat().st_size / 1024
    print(f"{hedef}  ({sayi} dosya, {boyut:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
