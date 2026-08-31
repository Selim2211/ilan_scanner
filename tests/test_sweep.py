"""Temizlik turu: link kontrolu ve kapanan ilanin listeden dusmesi.

Buradaki ilk test bir regresyon bekcisi: `HttpClient.request()` 404'te hata
firlattigi icin `_inspect_link` durum kodunu hic goremiyordu ve silinmis ilan
sonsuza dek "aktif" kaliyordu. Kontrol `probe()` uzerinden yapilmali.
"""
from datetime import datetime, timezone

import httpx
import pytest

from scanner.dedupe import dedupe
from scanner.models import REMOTE, Project
from scanner.pipeline import _inspect_link
from scanner.storage import Storage
from scanner.sweep import LinkSweeper


class SahteIstemci:
    """`probe()` sozlesmesini taklit eder: yanit ne ise o, ag hatasinda None."""

    def __init__(self, yanitlar: dict):
        self.yanitlar = yanitlar
        self.istekler: list[str] = []

    def probe(self, url):
        self.istekler.append(url)
        return self.yanitlar.get(url)

    def close(self):
        pass


def yanit(status: int, body: str = "") -> httpx.Response:
    return httpx.Response(status_code=status, text=body,
                          request=httpx.Request("GET", "http://x/"))


def test_404_kapanmis_sayilir():
    client = SahteIstemci({"http://x/1": yanit(404)})
    verdict = _inspect_link(client, "http://x/1")
    assert verdict.closed is True
    assert verdict.status == 404


def test_403_kapatmaz_dogrulanamadi_der():
    """Bot duvari bizim erisimimizle ilgili; ilan yayinda olabilir."""
    client = SahteIstemci({"http://x/1": yanit(403)})
    verdict = _inspect_link(client, "http://x/1")
    assert verdict.closed is None
    assert verdict.blocked is True
    assert verdict.problem == ""


def test_kapandi_metni_yakalanir():
    client = SahteIstemci({"http://x/1": yanit(200, "<p>This job is no longer available</p>")})
    assert _inspect_link(client, "http://x/1").closed is True


def test_giris_duvari_kapatmaz_sorunlu_isaretler():
    client = SahteIstemci({"http://x/1": yanit(200, "Please log in to see this job")})
    verdict = _inspect_link(client, "http://x/1")
    assert verdict.closed is False
    assert verdict.problem == "giriş gerekiyor"


def test_ag_hatasi_karar_verdirmez():
    client = SahteIstemci({})            # probe None doner
    assert _inspect_link(client, "http://x/1").closed is None


def test_sunucu_hatasi_kapatmaz():
    client = SahteIstemci({"http://x/1": yanit(503)})
    assert _inspect_link(client, "http://x/1").closed is None


# --- tur ------------------------------------------------------------------

def ilan(no: int, source: str = "test") -> Project:
    """`fingerprint` bos kalirsa upsert hepsini tek satira yazar; dedupe doldurur."""
    project = Project(source=source, source_id=str(no), url=f"http://x/{no}",
                      title=f"SAP ABAP {no}", description="abap", work_mode=REMOTE,
                      posted_at=datetime.now(timezone.utc))
    return dedupe([project])[0]


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "t.db")
    s.upsert([ilan(1), ilan(2), ilan(3)])
    yield s
    s.close()


def kos(store, monkeypatch, tmp_path, yanitlar, filters=None, config=None, full=False):
    """Turu senkron kosturur; gercek HTTP istemcisi yerine sahtesi konur."""
    import scanner.sweep as sweep_mod

    client = SahteIstemci(yanitlar)
    monkeypatch.setattr(sweep_mod, "sweep_client", lambda config: client)
    sweeper = LinkSweeper(store.path, config or {"database": str(store.path)})
    sweeper.start(filters or {"min_score": 0}, by="test", full=full)
    sweeper.join(timeout=20)
    return sweeper.state, client


def test_tur_yalnizca_404u_kapatir(store, monkeypatch, tmp_path):
    state, _ = kos(store, monkeypatch, tmp_path, {
        "http://x/1": yanit(404),          # kapanmis
        "http://x/2": yanit(403),          # dogrulanamadi
        "http://x/3": yanit(200, "SAP ABAP Developer aranıyor"),
    })
    assert (state.checked, state.closed, state.unverified, state.alive) == (3, 1, 1, 1)

    durumlar = {r["fingerprint"]: r for r in store.all_rows()}
    kapanan = [r for r in durumlar.values() if r["url"] == "http://x/1"][0]
    engelli = [r for r in durumlar.values() if r["url"] == "http://x/2"][0]
    yayinda = [r for r in durumlar.values() if r["url"] == "http://x/3"][0]
    assert kapanan["is_active"] == 0 and kapanan["closed_at"]
    assert engelli["is_active"] == 1
    assert yayinda["is_active"] == 1


def test_full_modu_sweep_max_sinirini_asar(store, monkeypatch, tmp_path):
    """'Tumunu kontrol et' dugmesi full=True gonderir: o an aktif olan ne kadar
    ilan varsa hepsine bakilmali, sweep_max'ta kalan kuyruk bir sonraki tura
    kalmamali."""
    config = {"database": str(store.path), "freshness": {"sweep_max": 2}}  # 3 ilan var
    state, _ = kos(store, monkeypatch, tmp_path, {
        "http://x/1": yanit(200, "SAP ABAP"),
        "http://x/2": yanit(200, "SAP ABAP"),
        "http://x/3": yanit(200, "SAP ABAP"),
    }, config=config, full=True)
    assert state.total == 3
    assert state.checked == 3


def test_full_olmayan_tur_sweep_max_ile_sinirlanir(store, monkeypatch, tmp_path):
    """Eski davranis korunmali: full=False iken sweep_max hala gecerli."""
    config = {"database": str(store.path), "freshness": {"sweep_max": 2}}
    state, _ = kos(store, monkeypatch, tmp_path, {
        "http://x/1": yanit(200, "SAP ABAP"),
        "http://x/2": yanit(200, "SAP ABAP"),
        "http://x/3": yanit(200, "SAP ABAP"),
    }, config=config, full=False)
    assert state.total == 2


def test_dogrulanamayan_ilan_bakildi_sayilmaz(store, monkeypatch, tmp_path):
    """403 alan ilana `verified_at` yazilmamali: yoksa otomatik dogrulama
    sirasinin sonuna atilir ve hicbir zaman gercekten kontrol edilmez."""
    kos(store, monkeypatch, tmp_path, {
        "http://x/1": yanit(200, "SAP ABAP"),
        "http://x/2": yanit(403),
        "http://x/3": yanit(404),
    })
    bakilan = {r["url"]: r["verified_at"] for r in store.all_rows()}
    assert bakilan["http://x/1"] and bakilan["http://x/3"]
    assert bakilan["http://x/2"] is None


def test_kapanan_ilan_bildirim_birakir(store, monkeypatch, tmp_path):
    kos(store, monkeypatch, tmp_path, {"http://x/1": yanit(410),
                                       "http://x/2": yanit(200, "SAP"),
                                       "http://x/3": yanit(200, "SAP")})
    kinds = [n["kind"] for n in store.notifications(limit=50)]
    assert kinds.count("closed") == 1


def test_filtre_kapsami_daraltir(store, monkeypatch, tmp_path):
    """Kullanici ne filtreliyorsa o kontrol edilir: kaynak disi ilana gidilmez."""
    store.upsert([ilan(9, source="jooble")])
    _, client = kos(store, monkeypatch, tmp_path,
                    {f"http://x/{n}": yanit(200, "SAP") for n in (1, 2, 3, 9)},
                    filters={"min_score": 0, "source": "jooble"})
    assert client.istekler == ["http://x/9"]


def test_elle_konmus_isaret_ezilmez(store, monkeypatch, tmp_path):
    row = store.all_rows()[0]
    store.set_flag(row["fingerprint"], "elle işaretlendi", by="admin")
    kos(store, monkeypatch, tmp_path,
        {r["url"]: yanit(200, "SAP ABAP") for r in store.all_rows()})
    guncel = store.get_by_fingerprint(row["fingerprint"])
    assert guncel["quality_flag"] == "problem"
    assert guncel["flagged_by"] == "admin"


# --- Jooble: link yerine API ile dogrulama --------------------------------

class SahteJoobleApi:
    """Jooble API'sinin POST arayuzunu taklit eder.

    `id` alanini BILEREK degistirir: gercek API ayni ilana sorgudan sorguya
    farkli id veriyor, bu yuzden eslestirme parmak iziyle yapilmali.
    """

    def __init__(self, kayitlar: list[dict] | None = None, hata: bool = False):
        self.kayitlar = kayitlar or []
        self.hata = hata
        self.istekler: list[dict] = []

    def post(self, url, json=None, **kwargs):
        self.istekler.append(json or {})
        if self.hata:
            raise RuntimeError("HTTP 403 - engellendi")
        return SahteJson({"jobs": self.kayitlar})

    def close(self):
        pass


class SahteJson:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def jooble_kaydi(title, company, job_id="9999"):
    return {"id": job_id, "title": title, "company": company, "location": "Remote",
            "link": "https://jooble.org/jdp/" + job_id, "snippet": "abap", "updated": None}


def jooble_source(client, monkeypatch):
    from scanner.sources.jooble import JoobleSource

    monkeypatch.setenv("JOOBLE_API_KEY", "test-key")
    return JoobleSource(client, {})


def test_jooble_farkli_id_ile_donse_de_yayinda_sayilir(store, monkeypatch):
    """Asil tuzak: id sorguya gore degisiyor, eslestirme parmak izi ile yapilir."""
    row = store.all_rows()[0]
    api = SahteJoobleApi([jooble_kaydi(row["title"], row["company"] or "", job_id="baska-id")])
    sonuc = jooble_source(api, monkeypatch).verify_alive([row])
    assert sonuc[row["fingerprint"]] is True


def test_jooble_indekste_yoksa_false_doner(store, monkeypatch):
    row = store.all_rows()[0]
    api = SahteJoobleApi([jooble_kaydi("Bambaska bir ilan", "Baska Firma")])
    assert jooble_source(api, monkeypatch).verify_alive([row])[row["fingerprint"]] is False


def test_jooble_api_hata_verirse_karar_verilmez(store, monkeypatch):
    row = store.all_rows()[0]
    api = SahteJoobleApi(hata=True)
    assert jooble_source(api, monkeypatch).verify_alive([row])[row["fingerprint"]] is None


def test_ilk_kacirma_kapatmaz_ikincisi_kapatir(store, monkeypatch):
    """Baslik aramasi ilani ~%13 kaciriyor; tek bulunamama kapatmaya yetmemeli."""
    from scanner.pipeline import verify_with_source

    row = store.all_rows()[0]
    api = SahteJoobleApi([jooble_kaydi("Alakasiz", "Firma")])
    source = jooble_source(api, monkeypatch)

    kapatilan, yayinda, bilinmiyor = verify_with_source(store, source, [row], miss_threshold=2)
    assert (kapatilan, yayinda, bilinmiyor) == ([], 0, 0)
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 1
    assert store.get_by_fingerprint(row["fingerprint"])["api_miss_streak"] == 1

    kapatilan, _, _ = verify_with_source(store, source, [row], miss_threshold=2)
    assert len(kapatilan) == 1
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 0


def test_arada_bulunursa_sayac_sifirlanir(store, monkeypatch):
    from scanner.pipeline import verify_with_source

    row = store.all_rows()[0]
    kayip = jooble_source(SahteJoobleApi([jooble_kaydi("Alakasiz", "Firma")]), monkeypatch)
    verify_with_source(store, kayip, [row], miss_threshold=2)
    assert store.get_by_fingerprint(row["fingerprint"])["api_miss_streak"] == 1

    bulundu = jooble_source(
        SahteJoobleApi([jooble_kaydi(row["title"], row["company"] or "")]), monkeypatch)
    _, yayinda, _ = verify_with_source(store, bulundu, [row], miss_threshold=2)
    assert yayinda == 1
    guncel = store.get_by_fingerprint(row["fingerprint"])
    assert guncel["api_miss_streak"] == 0 and guncel["is_active"] == 1


def test_ilan_basina_tek_istek_atilir(store, monkeypatch):
    from scanner.pipeline import verify_with_source

    rows = store.all_rows()
    api = SahteJoobleApi([])
    verify_with_source(store, jooble_source(api, monkeypatch), rows, miss_threshold=2)
    assert len(api.istekler) == len(rows)
    assert api.istekler[0]["ResultOnPage"] == "100"


def test_temizlik_turu_403_alan_joobleyi_apiye_sorar(store, monkeypatch, tmp_path):
    """Linki 403 donen Jooble ilani artik 'dogrulanamadi' deyip gecilmiyor."""
    import scanner.sweep as sweep_mod

    store.upsert([ilan(9, source="jooble")])
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]
    api = SahteJoobleApi([jooble_kaydi(row["title"], row["company"] or "", job_id="degisik")])
    monkeypatch.setenv("JOOBLE_API_KEY", "test-key")
    monkeypatch.setattr(sweep_mod, "build_client", lambda config: api)

    state, _ = kos(store, monkeypatch, tmp_path, {"http://x/9": yanit(403)},
                   filters={"min_score": 0, "source": "jooble"})
    assert state.api_checked == 1
    assert state.unverified == 0          # API cevap verdi: artik bilinmiyor degil
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 1


def test_full_sweep_supheliyi_apiye_sormadan_kapatir(store, monkeypatch, tmp_path):
    """close_suspects_immediately acikken 'KONTROL EDILIYOR' esigini gecmis ilan
    Jooble API'sine hic sorulmadan kapatilir - kullanici gozlemi: bu ilanlar
    neredeyse hep gercekten kapanmis cikiyor, API kotasini bosa harcamasin."""
    store.upsert([ilan(9, source="jooble")])
    store.mark_missing("jooble", [])          # streak = 1
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]

    config = {"database": str(store.path),
             "freshness": {"suspect_after_missing_scans": 1,
                            "close_suspects_immediately": True}}
    state, _ = kos(store, monkeypatch, tmp_path, {"http://x/9": yanit(403)},
                   filters={"min_score": 0, "source": "jooble"}, config=config)

    assert state.closed == 1
    assert state.api_checked == 0             # API'ye hic sorulmadi
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 0


def test_full_sweep_bayrak_kapaliyken_eski_davranis_korunur(store, monkeypatch, tmp_path):
    """close_suspects_immediately varsayilan (kapali) iken supheli ilan hala
    API'ye soruluyor - dogrudan kapatilmiyor."""
    store.upsert([ilan(9, source="jooble")])
    store.mark_missing("jooble", [])
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]

    import scanner.sweep as sweep_mod
    api = SahteJoobleApi([jooble_kaydi(row["title"], row["company"] or "")])
    monkeypatch.setenv("JOOBLE_API_KEY", "test-key")
    monkeypatch.setattr(sweep_mod, "build_client", lambda config: api)

    config = {"database": str(store.path), "freshness": {"suspect_after_missing_scans": 1}}
    state, _ = kos(store, monkeypatch, tmp_path, {"http://x/9": yanit(403)},
                   filters={"min_score": 0, "source": "jooble"}, config=config)

    assert state.api_checked == 1
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 1  # yayinda cikti


def test_otomatik_tur_joobleyi_korlemesine_kapatmaz(store, monkeypatch):
    """`verify_skip_sources` kaynaklari once API'ye sorulur, close_stale'e degil."""
    from scanner.pipeline import _resolve_missing

    store.upsert([ilan(9, source="jooble")])
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]
    api = SahteJoobleApi([jooble_kaydi(row["title"], row["company"] or "")])
    monkeypatch.setenv("JOOBLE_API_KEY", "test-key")
    config = {"sources": {"jooble": {"enabled": True}}}

    for _ in range(8):        # eski kural 6 turda kapatirdi
        _resolve_missing(store, api, kept=[], healthy_sources={"jooble"}, suspect_after=1,
                         close_unverifiable_after=6, skip_sources={"jooble"}, check_limit=10,
                         config=config, api_verify_limit=60, api_miss_threshold=2)
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 1

# --- kapanan ilanlarin listeden dusmesi -------------------------------------

def test_uzun_suredir_kayip_ilan_tek_api_kacirmasiyla_kapanir(store, monkeypatch):
    """Iki BAGIMSIZ sinyal ust uste gelince ikinci tur beklenmez.

    Regresyon: 1.650 Jooble ilani icin tur basina 60 dogrulama + 2 tur bekleme
    ~28 tur demekti; kapanmis ilanlar listede kaliyordu. Ilan hem taramada
    N turdur gorunmuyorsa HEM API'de yoksa tek kacirma yeter.
    """
    from scanner.pipeline import verify_with_source

    store.upsert([ilan(21, source="jooble")])
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]
    for _ in range(3):                                 # taramada 3 tur gorunmedi
        store.mark_missing("jooble", [])               # streak = 3

    row = store.get_by_fingerprint(row["fingerprint"])
    api = SahteJoobleApi([])                           # API ilani BULAMIYOR
    kaynak = jooble_source(api, monkeypatch)

    kapatilan, _, _ = verify_with_source(store, kaynak, [row], miss_threshold=2,
                                         kayip_esigi=3)
    assert len(kapatilan) == 1
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 0


def test_taze_ilan_yine_iki_tur_bekler(store, monkeypatch):
    """Kayip olmayan ilan tek kacirmayla kapanmamali - baslik aramasi ~%13 kaciriyor."""
    from scanner.pipeline import verify_with_source

    store.upsert([ilan(22, source="jooble")])
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]
    api = SahteJoobleApi([])
    kaynak = jooble_source(api, monkeypatch)

    kapatilan, _, _ = verify_with_source(store, kaynak, [row], miss_threshold=2,
                                         kayip_esigi=3)
    assert kapatilan == []                             # streak=0, tek kacirma yetmez
    assert store.get_by_fingerprint(row["fingerprint"])["is_active"] == 1


def test_api_bulursa_kayip_ilan_kapanmaz(store, monkeypatch):
    """Uzun suredir kayip olsa bile API "yayinda" diyorsa ilan KAPANMAZ."""
    from scanner.pipeline import verify_with_source

    store.upsert([ilan(23, source="jooble")])
    row = [r for r in store.all_rows() if r["source"] == "jooble"][0]
    for _ in range(5):
        store.mark_missing("jooble", [])
    row = store.get_by_fingerprint(row["fingerprint"])

    api = SahteJoobleApi([jooble_kaydi(row["title"], row["company"] or "")])
    kapatilan, yayinda, _ = verify_with_source(store, jooble_source(api, monkeypatch),
                                               [row], miss_threshold=2, kayip_esigi=3)
    assert kapatilan == [] and yayinda == 1
    guncel = store.get_by_fingerprint(row["fingerprint"])
    assert guncel["is_active"] == 1
    assert guncel["missing_streak"] == 0               # yayinda bulundu, sayac sifirlandi


def test_elle_tur_daha_cok_ilana_bakar():
    """Kullanici bilerek bastiginda sinir yukselmeli."""
    from scanner.pipeline import api_verify_settings

    config = {"freshness": {"api_verify_limit": 60, "api_verify_limit_manual": 150}}
    assert api_verify_settings(config)[0] == 60                 # otomatik tur
    assert api_verify_settings(config, manual=True)[0] == 150   # elle tur

def test_api_dogrulamasi_parcali_ilerler_ve_durdurulabilir(store, monkeypatch, tmp_path):
    """Uzun API asamasi sirasinda sayac kipirdamali, Durdur da is gormeli.

    Regresyon: tek seferde 150 ilan sorulunca ~4.5 dakika boyunca ilerleme
    cubugu donmus gorunuyor ve iptal bayragina hic bakilmiyordu.
    """
    import scanner.sweep as sweep_mod
    from scanner.sweep import LinkSweeper

    monkeypatch.setattr(sweep_mod, "API_PARCA", 2)

    ilanlar = [ilan(no, source="jooble") for no in range(40, 47)]
    store.upsert(ilanlar)
    rows = [r for r in store.all_rows() if r["source"] == "jooble"]

    sweeper = LinkSweeper(tmp_path / "t.db")
    gorulen_parcalar = []

    class SahteKaynak:
        name = "jooble"

        def verify_alive(self, satirlar):
            gorulen_parcalar.append(len(satirlar))
            # ucuncu parcada kullanici Durdur'a basiyor
            if len(gorulen_parcalar) == 3:
                sweeper._cancel.set()
            return {r["fingerprint"]: True for r in satirlar}

    monkeypatch.setattr(sweep_mod, "verifier_for", lambda *a, **k: SahteKaynak())
    monkeypatch.setattr(sweep_mod, "build_client", lambda config: SahteIstemci({}))

    sweeper._api_dogrula(store, rows, {"freshness": {"api_verify_limit_manual": 100}})

    assert gorulen_parcalar[:3] == [2, 2, 2]        # 150'lik tek yigin degil
    assert len(gorulen_parcalar) == 3               # iptalden sonra devam etmedi
    assert sweeper.state.api_checked == 6           # sayac parca parca ilerledi
