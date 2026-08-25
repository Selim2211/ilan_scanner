"""Paketlenmis programin giris noktasi.

Kurulu exe cift tiklandiginda dogrudan masaustu penceresi acilir.
`--minimized` ile tepside baslar (Windows acilisinda otomatik baslatma bunu kullanir).
"""
from __future__ import annotations

import sys


def main() -> int:
    minimized = "--minimized" in sys.argv
    port = None
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except (IndexError, ValueError):
            port = None

    if "--tray" in sys.argv:
        from scanner.desktop.tray import main as tray_main

        return tray_main(port=port)

    from scanner.desktop.launcher import main as gui_main

    return gui_main(port=port, minimized=minimized)


if __name__ == "__main__":
    sys.exit(main())
