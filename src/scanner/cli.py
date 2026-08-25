"""Komut satiri arayuzu.

    py -m scanner scan [--source jooble] [--dry-run] [--query "SAP ABAP remote"]
    py -m scanner export [--csv] [--remote] [--contract] [--min-score N]
    py -m scanner mail [--dry-run]
    py -m scanner verify        # aktif ilanlarin linkini kontrol et, kapananlari kapat
    py -m scanner sweep [--limit N] [--min-score N]   # temizlik turu: paralel link kontrolu
    py -m scanner notifications [--unread] [--read-all]
    py -m scanner rescore
    py -m scanner upwork-auth [--status]   # Upwork token'i al / durumunu goster
    py -m scanner gui                      # masaustu penceresi + tepsi ikonu
    py -m scanner tray                     # pencere yok, sadece tepsi: baslat/durdur
    py -m scanner mini                     # kucuk pencere: tek buton, baslat/durdur
    py -m scanner serve [--port 8000] [--no-auto] [--interval 30]
    py -m scanner watch [--interval 30]   # panelsiz surekli tarama dongusu
    py -m scanner stats
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import load_config, load_keywords, resolve_path
from .export import default_name, to_csv, to_xlsx
from .pipeline import run_scan, verify_active
from .storage import Storage

MODE_SHORT = {"remote": "UZAKTAN", "hybrid": "hibrit ", "onsite": "yerinde", "unknown": "   ?   "}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scan(args: argparse.Namespace) -> int:
    result = run_scan(only_sources=args.source, dry_run=args.dry_run, queries=args.query)
    remote = sum(1 for p in result.projects if p.work_mode == "remote")
    hybrid = sum(1 for p in result.projects if p.work_mode == "hybrid")
    contract = sum(1 for p in result.projects if p.is_contract)
    print()
    print(f"Cekilen ilan      : {result.fetched}")
    print(f"Tekillestirilmis  : {result.kept}   (uzaktan {remote} · hibrit {hybrid} · proje bazli {contract})")
    print(f"Yeni ilan         : {result.new}")
    print(f"Guncellenen       : {result.updated}")
    print(f"Kapanan ilan      : {result.closed}")
    if result.errors:
        print("\nHatalar:")
        for source, error in result.errors:
            print(f"  ! {source}: {error}")
    print("\nEn iyi projeler:")
    for project in result.projects[:20]:
        mode = MODE_SHORT.get(project.work_mode, "?")
        budget = (project.budget_raw or "")[:16]
        print(f"  [{project.score:>3}] {mode} | {project.title[:46]:<46} | "
              f"{project.company[:20]:<20} | {budget:<16} | {project.source}")
    if args.dry_run:
        print("\n(dry-run: veritabanina yazilmadi)")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    config = load_config()
    storage = Storage(resolve_path(config["database"]))
    min_score = args.min_score if args.min_score is not None else int(load_keywords().get("min_score", 0))
    work_mode = "remote" if args.remote else (args.mode or None)
    rows = storage.query(min_score=min_score, only_new=args.new, source=args.source,
                         search=args.search, work_mode=work_mode, contract_only=args.contract,
                         shortlist=args.shortlist, country=args.country,
                         max_age_days=args.days, has_budget=args.has_budget,
                         include_closed=args.include_closed, exclude=args.exclude,
                         include_supply=args.include_supply, limit=args.limit)
    out_dir = resolve_path(config.get("output_dir", "output"))
    fmt = "csv" if args.csv else "xlsx"
    path = Path(args.out) if args.out else out_dir / default_name("sap_projeleri", fmt)
    (to_csv if args.csv else to_xlsx)(rows, path)
    storage.close()
    print(f"{len(rows)} proje yazildi -> {path}")
    return 0


def cmd_mail(args: argparse.Namespace) -> int:
    from . import mailer

    config = load_config()
    mail_cfg = config.get("mail", {})
    storage = Storage(resolve_path(config["database"]))
    rows = storage.query(min_score=int(mail_cfg.get("min_score", 0)),
                         only_new=bool(mail_cfg.get("only_new", True)), limit=200)
    if not rows:
        print("Gonderilecek yeni proje yok.")
        storage.close()
        return 0

    attachment = None
    if mail_cfg.get("attach_excel", True):
        attachment = resolve_path(config.get("output_dir", "output")) / default_name("sap_projeleri", "xlsx")
        to_xlsx(rows, attachment)

    msg = mailer.build_message(rows, mail_cfg.get("subject", "SAP Proje Radari - {date}"), attachment)
    if args.dry_run:
        preview = resolve_path(config.get("output_dir", "output")) / "mail_onizleme.html"
        preview.write_text(mailer.render_html(rows, msg["Subject"]), encoding="utf-8")
        print(f"{len(rows)} proje. Onizleme -> {preview} (gonderim yapilmadi)")
    else:
        mailer.send(msg)
        storage.mark_notified([row["fingerprint"] for row in rows])
        print(f"{len(rows)} proje gonderildi -> {msg['To']}")
    storage.close()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Aktif ilanlarin linkini acip kapanmis olanlari isaretler."""
    config = load_config()
    limit = args.limit or int((config.get("freshness") or {}).get("verify_limit", 40))
    checked, closed = verify_active(limit=limit, dry_run=args.dry_run)
    print(f"{checked} ilan kontrol edildi, {closed} tanesi kapanmis"
          + (" (dry-run: degisiklik yazilmadi)" if args.dry_run else ""))
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Temizlik turu: aktif ilanlarin linkini PARALEL acar, kapananlari kapatir.

    `verify`den farki: sira beklemez (verified_at'e bakmaz), kaynak atlamaz ve
    filtre alabilir - panelin "Listeyi kontrol et" dugmesiyle ayni motor.
    """
    from .sweep import run_sweep

    filters = {"min_score": args.min_score or 0}
    if args.source:
        filters["source"] = args.source
    if args.mode:
        filters["work_mode"] = args.mode
    state = run_sweep(filters, limit=args.limit)
    print(f"{state.checked} ilan kontrol edildi: {state.closed} kapandi, "
          f"{state.flagged} sorunlu, {state.unverified} dogrulanamadi")
    return 0


def cmd_notifications(args: argparse.Namespace) -> int:
    config = load_config()
    storage = Storage(resolve_path(config["database"]))
    if args.read_all:
        storage.mark_notifications_read()
        print("Tum bildirimler okundu olarak isaretlendi")
        storage.close()
        return 0

    rows = storage.notifications(unread_only=args.unread, limit=args.limit)
    print(f"{storage.unread_count()} okunmamis bildirim\n")
    for row in rows:
        mark = " " if row["read_at"] else "*"
        kind = "YENI " if row["kind"] == "new" else "KAPANDI"
        print(f"{mark} [{row['created_at'][:16].replace('T', ' ')}] {kind} "
              f"({row['score']:>3}) {row['title'][:60]}")
        if row["detail"]:
            print(f"      {row['detail'][:80]}")
    storage.close()
    return 0


def cmd_rescore(_args: argparse.Namespace) -> int:
    """keywords.yaml degistiginde kayitli ilanlari ag'a cikmadan yeniden skorlar."""
    from .pipeline import rescore_all

    print(f"{rescore_all()} ilan yeniden skorlandi")
    return 0


def cmd_upwork_auth(args: argparse.Namespace) -> int:
    """Upwork OAuth2 token'ini alir; sonrasinda yenileme kendiliginden yapilir."""
    from .sources import upwork_auth

    if args.status:
        info = upwork_auth.token_status()
        if info["source"] == "yok":
            print("Token yok. 'py -m scanner upwork-auth' calistirin.")
            return 1
        left = info["seconds_left"]
        omur = "sinirsiz (env)" if left is None else f"{left // 60} dk"
        print(f"Kaynak    : {info['source']}")
        print(f"Akis      : {info['grant']}")
        print(f"Kalan omur: {omur}")
        print(f"Yenileme  : {'var' if info['has_refresh'] else 'yok (client_credentials)'}")
        return 0

    client_id, secret, redirect = upwork_auth._credentials()
    if not client_id or not secret:
        print("Once .env dosyasina Upwork uygulama bilgilerini ekleyin:")
        print("  UPWORK_CLIENT_ID=...")
        print("  UPWORK_CLIENT_SECRET=...")
        if redirect:
            print(f"  UPWORK_REDIRECT_URI={redirect}   # masaustu tipi anahtarda bos birakilabilir")
        print("\nAnahtarlar: https://www.upwork.com/developer/keys/apply")
        return 1

    upwork_auth.reset_backoff()
    if args.code:
        data = upwork_auth.exchange_code(args.code)
        print(f"Token alindi, {int(data['expires_at'] - time.time()) // 60} dk gecerli.")
        return 0

    try:
        data = upwork_auth.client_credentials()
    except Exception as exc:  # noqa: BLE001 - etkilesimsiz akis her uygulamada acik degil
        print(f"client_credentials calismadi: {exc}")
        print()
        print("Yetkilendirme kodu akisina geciliyor. Su adresi tarayicida ac:")
        print()
        print(f"  {upwork_auth.authorization_url()}")
        print()
        print("Onayladiktan sonra adres cubugundaki code=... degerini kopyala ve calistir:")
        print("  py -m scanner upwork-auth --code <KOD>")
        return 1

    print(f"Token alindi (client_credentials), {int(data['expires_at'] - time.time()) // 60} dk gecerli.")
    print("Suresi dolunca ajan kendisi yeniler.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web.app import create_app

    app = create_app(auto_scan=not args.no_auto, interval_minutes=args.interval)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Masaustu baslatma penceresi (patronun kullandigi arayuz)."""
    from .desktop.launcher import main as gui_main

    return gui_main(port=args.port, minimized=args.minimized)


def cmd_tray(args: argparse.Namespace) -> int:
    """Pencere yok, sadece sistem tepsisi: tek islevi baslat/durdur."""
    from .desktop.tray import main as tray_main

    return tray_main(port=args.port)


def cmd_mini(args: argparse.Namespace) -> int:
    """Kucuk pencere: tek buton, baslat/durdur, baska hicbir sey yok."""
    from .desktop.mini import main as mini_main

    return mini_main(port=args.port)


def cmd_watch(args: argparse.Namespace) -> int:
    """Panel acmadan surekli tarama: terminalde birakilir, veri akmaya devam eder."""
    import time

    from .scheduler import from_config

    scanner = from_config(override_minutes=args.interval, enabled=True)
    assert scanner is not None
    scanner.start()
    print(f"Surekli tarama basladi (her {scanner.state.interval_minutes} dk). Durdurmak icin Ctrl+C.")
    last_reported = None
    try:
        while True:
            time.sleep(5)
            state = scanner.snapshot()
            if state["phase"] == "sleeping" and state["last_finished"] != last_reported:
                last_reported = state["last_finished"]
                print(f"{last_reported[11:19]} tarama bitti - yeni {state['last_new']}, "
                      f"guncel {state['last_updated']}, kapanan {state['last_closed']}; "
                      f"sonraki tarama {state['interval_minutes']} dk sonra")
                for error in state["errors"]:
                    print(f"  ! {error}")
    except KeyboardInterrupt:
        print("durduruluyor...")
        scanner.stop()
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    config = load_config()
    storage = Storage(resolve_path(config["database"]))
    s = storage.stats()
    print(f"Aktif ilan: {s['active']}   Kapanan: {s['closed']}   Yeni: {s['new']}")
    print(f"Uzaktan: {s['remote']}   Hibrit: {s['hybrid']}   Proje bazli: {s['contract']}")
    if s.get("supply"):
        print(f"Arz (hizmet satan ilan): {s['supply']} - varsayilan listede gizli")
    print(f"Takipte: {s['shortlist']}   Basvurulan: {s['applied']}")
    print(f"Okunmamis bildirim: {storage.unread_count()}")
    print(f"Son tarama: {s['last_seen']}")
    print("\nSon calistirmalar:")
    for run in storage.recent_runs(12):
        suffix = f" - {run['error']}" if run["status"] != "ok" else ""
        print(f"  #{run['id']:<4} {run['source']:<14} '{(run['query'] or '')[:28]:<28}' "
              f"bulunan={run['fetched']:<4} {run['status']}{suffix}")
    storage.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scanner", description="SAP/ABAP proje radari")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="kaynaklari tara ve veritabanina yaz")
    p_scan.add_argument("--source", action="append", help="sadece bu kaynak")
    p_scan.add_argument("--query", action="append", help="config yerine bu arama metni")
    p_scan.add_argument("--dry-run", action="store_true", help="veritabanina yazma")
    p_scan.set_defaults(func=cmd_scan)

    p_export = sub.add_parser("export", help="Excel/CSV disari aktar")
    p_export.add_argument("--csv", action="store_true")
    p_export.add_argument("--xlsx", action="store_true", help="varsayilan")
    p_export.add_argument("--remote", action="store_true", help="sadece uzaktan projeler")
    p_export.add_argument("--mode", choices=["remote", "hybrid", "onsite", "unknown"])
    p_export.add_argument("--contract", action="store_true", help="sadece proje bazli / freelance")
    p_export.add_argument("--min-score", type=int)
    p_export.add_argument("--new", action="store_true")
    p_export.add_argument("--shortlist", action="store_true")
    p_export.add_argument("--country")
    p_export.add_argument("--source")
    p_export.add_argument("--search")
    p_export.add_argument("--limit", type=int, default=500)
    p_export.add_argument("--days", type=int, help="son N gunde yayinlanan ilanlar")
    p_export.add_argument("--has-budget", action="store_true", help="butce/ucret yazan ilanlar")
    p_export.add_argument("--include-closed", action="store_true", help="kapanmis ilanlari da yaz")
    p_export.add_argument("--include-supply", action="store_true",
                          help="hizmet satan (arz) ilanlari da yaz")
    p_export.add_argument("--exclude", help="bu kelimeleri iceren ilanlari ele (virgulle ayirin)")
    p_export.add_argument("--out")
    p_export.set_defaults(func=cmd_export)

    p_mail = sub.add_parser("mail", help="rapor maili gonder")
    p_mail.add_argument("--dry-run", action="store_true")
    p_mail.set_defaults(func=cmd_mail)

    p_verify = sub.add_parser("verify", help="aktif ilan linklerini kontrol et, kapananlari kapat")
    p_verify.add_argument("--limit", type=int)
    p_verify.add_argument("--dry-run", action="store_true")
    p_verify.set_defaults(func=cmd_verify)

    p_sweep = sub.add_parser("sweep", help="temizlik turu: linkleri paralel kontrol et")
    p_sweep.add_argument("--limit", type=int, help="en fazla kac ilan (varsayilan: sweep_max)")
    p_sweep.add_argument("--min-score", type=int, default=0)
    p_sweep.add_argument("--source")
    p_sweep.add_argument("--mode", choices=["remote", "hybrid", "onsite", "unknown"])
    p_sweep.set_defaults(func=cmd_sweep)

    p_notif = sub.add_parser("notifications", help="bildirim akisi")
    p_notif.add_argument("--unread", action="store_true", help="sadece okunmamislar")
    p_notif.add_argument("--read-all", action="store_true", help="hepsini okundu isaretle")
    p_notif.add_argument("--limit", type=int, default=30)
    p_notif.set_defaults(func=cmd_notifications)

    p_rescore = sub.add_parser("rescore", help="keywords.yaml degisince puanlari yeniden hesapla")
    p_rescore.set_defaults(func=cmd_rescore)

    p_upwork = sub.add_parser("upwork-auth", help="Upwork OAuth2 token al / durum")
    p_upwork.add_argument("--status", action="store_true", help="mevcut token'in durumu")
    p_upwork.add_argument("--code", help="tarayicidan alinan yetkilendirme kodu")
    p_upwork.set_defaults(func=cmd_upwork_auth)

    p_serve = sub.add_parser("serve", help="yerel web panelini baslat")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--no-auto", action="store_true", help="arka plan taramasini kapat")
    p_serve.add_argument("--interval", type=int, help="arka plan tarama araligi (dakika)")
    p_serve.set_defaults(func=cmd_serve)

    p_gui = sub.add_parser("gui", help="masaustu baslatma penceresi")
    p_gui.add_argument("--port", type=int, help="panel portu (varsayilan: bos olan ilk port)")
    p_gui.add_argument("--minimized", action="store_true", help="tepside baslat")
    p_gui.set_defaults(func=cmd_gui)

    p_tray = sub.add_parser("tray", help="pencere yok, sadece tepsi: baslat/durdur")
    p_tray.add_argument("--port", type=int, help="panel portu (varsayilan: bos olan ilk port)")
    p_tray.set_defaults(func=cmd_tray)

    p_mini = sub.add_parser("mini", help="kucuk pencere: tek buton, baslat/durdur")
    p_mini.add_argument("--port", type=int, help="panel portu (varsayilan: bos olan ilk port)")
    p_mini.set_defaults(func=cmd_mini)

    p_watch = sub.add_parser("watch", help="panelsiz surekli tarama dongusu")
    p_watch.add_argument("--interval", type=int, help="tarama araligi (dakika)")
    p_watch.set_defaults(func=cmd_watch)

    p_stats = sub.add_parser("stats", help="veritabani ozeti")
    p_stats.set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
