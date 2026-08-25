"""Adapter parse testleri - ag erisimi yok, kaydedilmis/ornek yanitlar kullanilir."""
import json

from scanner.models import HYBRID, REMOTE
from scanner.sources.upwork import UpworkSource


class FakeClient:
    """HttpClient yerine gecen sahte istemci."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls: list[tuple[str, object]] = []
        self._current = None

    def get(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, params or kwargs.get("json")))
        self._current = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return self

    def post(self, url, json=None, headers=None, **kwargs):
        self.calls.append((url, json))
        self._current = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return self

    @property
    def text(self):
        return self._current

    def json(self):
        return self._current


# --- upwork -----------------------------------------------------------
UPWORK_RESPONSE = {
    "data": {"marketplaceJobPostingsSearch": {"totalCount": 1, "edges": [{"node": {
        "id": "1234", "title": "SAP ABAP Developer for S/4HANA migration",
        "description": "<p>We need a <b>freelance</b> ABAP developer, remote.</p>",
        "ciphertext": "~01abc123", "duration": "3 to 6 months", "engagement": "30+ hrs/week",
        "experienceLevel": "EXPERT", "publishedDateTime": "2026-08-15T10:00:00Z",
        "amount": {"rawValue": 0, "currency": "USD"},
        "hourlyBudgetMin": {"rawValue": 45}, "hourlyBudgetMax": {"rawValue": 70},
        "skills": [{"name": "ABAP"}, {"name": "SAP S/4HANA"}],
        "client": {"location": {"country": "Germany"}},
        "job": {"contractTerms": {"contractType": "HOURLY"}},
    }}]}}
}


def test_upwork_parse(monkeypatch):
    monkeypatch.setenv("UPWORK_TOKEN", "test-token")
    client = FakeClient(UPWORK_RESPONSE)
    projects = UpworkSource(client, {"pages": 1, "page_size": 50}).fetch("SAP ABAP")

    assert len(projects) == 1
    project = projects[0]
    assert project.source == "upwork"
    assert project.url == "https://www.upwork.com/jobs/01abc123"
    assert project.work_mode == REMOTE
    assert project.is_contract is True
    assert project.budget_raw == "45-70 USD/saat"
    assert project.duration == "3 to 6 months"
    assert project.country == "Germany"
    assert project.skills == ["ABAP", "SAP S/4HANA"]
    assert project.posted_at.year == 2026
    assert "freelance" in project.description.lower()


def test_upwork_kimlik_yoksa_aciklayici_hata(monkeypatch, tmp_path):
    for name in ("UPWORK_TOKEN", "UPWORK_CLIENT_ID", "UPWORK_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UPWORK_TOKEN_FILE", str(tmp_path / "token.json"))
    projects, error = UpworkSource(FakeClient({}), {}).safe_fetch("abap")
    assert projects == [] and "UPWORK_CLIENT_ID" in error


def test_upwork_graphql_hatasi_yakalanir(monkeypatch):
    monkeypatch.setenv("UPWORK_TOKEN", "test-token")
    client = FakeClient({"errors": [{"message": "Unauthorized"}]})
    projects, error = UpworkSource(client, {"pages": 1}).safe_fetch("abap")
    assert projects == [] and "GraphQL" in error


def test_upwork_bearer_token_gonderir(monkeypatch):
    monkeypatch.setenv("UPWORK_TOKEN", "test-token")
    client = FakeClient(UPWORK_RESPONSE)
    UpworkSource(client, {"pages": 1}).fetch("SAP ABAP")
    url, payload = client.calls[0]
    assert url == "https://api.upwork.com/graphql"
    assert payload["variables"]["filter"]["searchExpression_eq"] == "SAP ABAP"


# --- upwork token yonetimi -------------------------------------------
def _upwork_env(monkeypatch, tmp_path):
    monkeypatch.delenv("UPWORK_TOKEN", raising=False)
    monkeypatch.setenv("UPWORK_CLIENT_ID", "id-123")
    monkeypatch.setenv("UPWORK_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("UPWORK_TOKEN_FILE", str(tmp_path / "token.json"))


def _sahte_token_ucu(monkeypatch, *responses):
    """upwork_auth'un httpx.post cagrisini taklit eder; gonderilen payload'lari toplar."""
    from scanner.sources import upwork_auth

    upwork_auth.reset_backoff()          # testler birbirinin beklemesini devralmasin
    gonderilen: list[dict] = []
    kalan = list(responses)

    class SahteYanit:
        def __init__(self, body, status=200):
            self._body, self.status_code, self.text = body, status, str(body)

        def json(self):
            return self._body

    def sahte_post(url, data=None, timeout=None, headers=None):
        gonderilen.append(dict(data or {}))
        body, status = kalan.pop(0) if len(kalan) > 1 else kalan[0]
        return SahteYanit(body, status)

    monkeypatch.setattr(upwork_auth.httpx, "post", sahte_post)
    return gonderilen


def test_upwork_token_client_credentials_ile_alinir(monkeypatch, tmp_path):
    from scanner.sources import upwork_auth

    _upwork_env(monkeypatch, tmp_path)
    gonderilen = _sahte_token_ucu(monkeypatch, ({"access_token": "abc", "expires_in": 86400}, 200))

    assert upwork_auth.get_token() == "abc"
    assert gonderilen[0]["grant_type"] == "client_credentials"
    assert gonderilen[0]["client_id"] == "id-123"
    # ikinci cagri agi tekrar yormaz: depodan okunur
    assert upwork_auth.get_token() == "abc"
    assert len(gonderilen) == 1


def test_upwork_suresi_dolan_token_yenilenir(monkeypatch, tmp_path):
    """Ajan gunlerce calisiyor: token dolunca refresh_token ile sessizce yenilenmeli."""
    from scanner.sources import upwork_auth

    _upwork_env(monkeypatch, tmp_path)
    upwork_auth.save_stored({"access_token": "eski", "refresh_token": "r-1",
                             "expires_at": 0, "grant": "authorization_code"})
    gonderilen = _sahte_token_ucu(monkeypatch, ({"access_token": "yeni", "expires_in": 86400}, 200))

    assert upwork_auth.get_token() == "yeni"
    assert gonderilen[0]["grant_type"] == "refresh_token"
    assert gonderilen[0]["refresh_token"] == "r-1"
    assert upwork_auth.load_stored()["access_token"] == "yeni"


def test_upwork_yenileme_basarisizsa_hata(monkeypatch, tmp_path):
    from scanner.sources import upwork_auth
    from scanner.sources.base import FetchError

    _upwork_env(monkeypatch, tmp_path)
    upwork_auth.save_stored({"access_token": "eski", "refresh_token": "r-1",
                             "expires_at": 0, "grant": "authorization_code"})
    _sahte_token_ucu(monkeypatch, ({"error_description": "invalid_grant"}, 400))

    try:
        upwork_auth.get_token()
    except FetchError as exc:
        assert "invalid_grant" in str(exc)
    else:
        raise AssertionError("FetchError bekleniyordu")


def test_upwork_401_alinca_token_yenilenip_tekrar_denenir(monkeypatch, tmp_path):
    """Token istek sirasinda dolarsa tarama bir 401 yuzunden bos donmemeli."""
    from scanner.sources.base import BlockedError

    _upwork_env(monkeypatch, tmp_path)
    _sahte_token_ucu(monkeypatch, ({"access_token": "t-2", "expires_in": 86400}, 200))

    class BirKez401:
        def __init__(self):
            self.headers: list[dict] = []

        def post(self, url, json=None, headers=None, **kwargs):
            self.headers.append(headers or {})
            if len(self.headers) == 1:
                raise BlockedError("HTTP 401 - engellendi")
            return self

        def json(self):
            return UPWORK_RESPONSE

    client = BirKez401()
    projects = UpworkSource(client, {"pages": 1}).fetch("SAP ABAP")

    assert len(projects) == 1
    assert len(client.headers) == 2                     # yenileyip tekrar denedi
    assert client.headers[1]["Authorization"] == "Bearer t-2"


def test_upwork_basarisiz_denemeden_sonra_beklenir(monkeypatch, tmp_path):
    """Anahtar Upwork incelemesindeyken her turda token istenmemeli."""
    from scanner.sources import upwork_auth
    from scanner.sources.base import FetchError

    _upwork_env(monkeypatch, tmp_path)
    gonderilen = _sahte_token_ucu(monkeypatch, ({"error": "invalid_client"}, 401))

    for _ in range(3):
        try:
            upwork_auth.get_token()
        except FetchError:
            pass

    assert len(gonderilen) == 1        # ilk denemeden sonra bekleme devrede


# --- reed -------------------------------------------------------------
REED_RESPONSE = {"results": [{
    "jobId": 991, "jobTitle": "SAP FICO Consultant - Outside IR35",
    "employerName": "Acme Recruitment", "locationName": "Manchester",
    "jobDescription": "6 month contract, hybrid working 2 days on site.",
    "date": "14/08/2026", "minimumSalary": 500, "maximumSalary": 600,
    "currency": "GBP", "contractType": "contract", "fullTime": True,
    "jobUrl": "https://www.reed.co.uk/jobs/sap-fico/991",
}]}


def test_reed_parse(monkeypatch):
    from scanner.sources.reed import ReedSource

    monkeypatch.setenv("REED_API_KEY", "anahtar")
    projects = ReedSource(FakeClient(REED_RESPONSE), {"pages": 1}).fetch("SAP")

    assert len(projects) == 1
    p = projects[0]
    assert p.source == "reed" and p.country == "United Kingdom"
    assert p.is_contract is True
    assert p.budget_raw == "500-600 GBP"
    assert p.work_mode == HYBRID                 # "hybrid working" aciklamadan
    assert p.posted_at.month == 8 and p.posted_at.day == 14


def test_reed_anahtari_basic_auth_ile_gonderilir(monkeypatch):
    from scanner.sources.reed import ReedSource

    monkeypatch.setenv("REED_API_KEY", "anahtar")

    class AuthYakala(FakeClient):
        def get(self, url, params=None, headers=None, **kwargs):
            self.auth = kwargs.get("auth")
            return super().get(url, params=params, headers=headers, **kwargs)

    client = AuthYakala(REED_RESPONSE)
    ReedSource(client, {"pages": 1}).fetch("SAP")
    assert client.auth == ("anahtar", "")


# --- careerjet --------------------------------------------------------
CAREERJET_RESPONSE = {"type": "JOBS", "hits": 1, "jobs": [{
    "title": "SAP ABAP Entwickler (Freelance)",
    "company": "IT Beratung GmbH", "locations": "München, Deutschland",
    "salary": "80-95 EUR/Stunde", "date": "2026-08-15",
    "description": "Remote möglich, Projektdauer 6 Monate, freelance.",
    "url": "https://www.careerjet.de/jobad/de123",
}]}


def test_careerjet_parse(monkeypatch):
    from scanner.sources.careerjet import CareerjetSource

    monkeypatch.setenv("CAREERJET_AFFID", "affid")
    projects = CareerjetSource(FakeClient(CAREERJET_RESPONSE),
                               {"locales": ["de_DE"], "pages": 1}).fetch("SAP ABAP")

    assert len(projects) == 1
    p = projects[0]
    assert p.source == "careerjet" and p.country == "Germany"
    assert p.is_contract is True                 # "freelance" gecen aciklama
    assert p.budget_raw == "80-95 EUR/Stunde"
    assert p.work_mode == REMOTE


def test_careerjet_api_hatasi_yakalanir(monkeypatch):
    from scanner.sources.careerjet import CareerjetSource

    monkeypatch.setenv("CAREERJET_AFFID", "affid")
    client = FakeClient({"type": "ERROR", "error": "invalid affid"})
    projects, error = CareerjetSource(client, {"locales": ["de_DE"], "pages": 1}).safe_fetch("sap")
    assert projects == [] and "invalid affid" in error


# --- ted (AB ihaleleri) ----------------------------------------------
TED_RESPONSE = {"totalNoticeCount": 1, "notices": [{
    "publication-number": "512345-2026",
    "notice-title": {"eng": "Germany – Systems consultancy – Migration SAP S/4HANA",
                     "deu": "Deutschland – Systemberatung – Migration SAP S/4HANA"},
    "buyer-name": {"deu": ["Stadtwerke Musterstadt GmbH"]},
    "buyer-country": ["DEU"],
    "publication-date": "2026-08-14+02:00",
    "deadline-receipt-tender-date-lot": ["2026-09-20+02:00"],
    "classification-cpv": ["72000000", "48000000"],
    "total-value": {"amount": 1386874, "currency": "EUR"},
    "description-lot": {"deu": ["Migration des SAP ERP auf S/4HANA inkl. ABAP Anpassungen."]},
}]}


def test_ted_parse():
    from scanner.sources.ted import TedSource

    projects = TedSource(FakeClient(TED_RESPONSE), {"pages": 1, "limit": 50}).fetch("SAP S/4HANA")

    assert len(projects) == 1
    p = projects[0]
    assert p.source == "ted"
    assert p.title == "Migration SAP S/4HANA"        # "ulke - kategori - baslik" kirpildi
    assert p.company == "Stadtwerke Musterstadt GmbH"
    assert p.country == "Germany"
    assert p.is_contract is True and p.engagement == "ihale"
    assert p.budget_raw == "1.386.874 EUR"
    assert p.duration == "son teklif: 2026-09-20"
    assert p.url == "https://ted.europa.eu/en/notice/-/detail/512345-2026"
    assert p.posted_at.day == 14


def test_ted_sorgusu_cpv_ve_tarih_filtresi_iceriyor():
    """Genis 'SAP' aramasi alakasiz ihale getiriyordu; CPV + tarih filtresi sart."""
    from scanner.sources.ted import TedSource

    client = FakeClient(TED_RESPONSE)
    TedSource(client, {"pages": 1, "max_days_old": 90}).fetch("ABAP")
    url, payload = client.calls[0]
    assert url == "https://api.ted.europa.eu/v3/notices/search"
    assert 'FT ~ ("ABAP")' in payload["query"]
    assert "classification-cpv IN (72000000 48000000)" in payload["query"]
    assert "today(-90)" in payload["query"]


def test_ted_beklenmeyen_yanit_hatasi():
    from scanner.sources.ted import TedSource

    projects, error = TedSource(FakeClient({"message": "bad request"}), {}).safe_fetch("SAP")
    assert projects == [] and "beklenmeyen yanit" in error


# --- jooble bolgeleri --------------------------------------------------
def test_jooble_bolgeleri_ayri_anahtar_ve_host_kullanir(monkeypatch):
    """Her Jooble alan adi AYRI anahtar ister; jooble.org anahtari uk/de'de 403 verir.

    Bolgeler ayri kaynak sinifi oldugu icin panelde ayri kart, ilanlarda ayri
    `source` etiketi olusur. Bu test yanlislikla ortak anahtara donulmesini tutar.
    """
    from scanner.sources import REGISTRY

    for env in ("JOOBLE_API_KEY", "JOOBLE_API_KEY_UK", "JOOBLE_API_KEY_DE"):
        monkeypatch.setenv(env, env.lower())

    beklenen = {
        "jooble": ("https://jooble.org/api/jooble_api_key", "JOOBLE_API_KEY"),
        "jooble-uk": ("https://uk.jooble.org/api/jooble_api_key_uk", "JOOBLE_API_KEY_UK"),
        "jooble-de": ("https://de.jooble.org/api/jooble_api_key_de", "JOOBLE_API_KEY_DE"),
    }
    for ad, (endpoint, env) in beklenen.items():
        source = REGISTRY[ad](client=None, options={})
        assert source._endpoint() == endpoint
        assert [v.name for v in source.env_requirements()] == [env]
        assert source.name == ad


def test_jooble_bolgesinin_anahtari_yoksa_kaynak_atlanir(monkeypatch):
    from scanner.pipeline import missing_credentials
    from scanner.sources import REGISTRY

    monkeypatch.setenv("JOOBLE_API_KEY", "var")
    monkeypatch.delenv("JOOBLE_API_KEY_DE", raising=False)
    assert missing_credentials(REGISTRY["jooble"](client=None, options={})) == []
    assert missing_credentials(REGISTRY["jooble-de"](client=None, options={})) == ["JOOBLE_API_KEY_DE"]


def test_jooble_bolge_ilanlari_kendi_kaynak_adiyla_kaydedilir(monkeypatch):
    from scanner.sources import REGISTRY

    monkeypatch.setenv("JOOBLE_API_KEY_DE", "de-key")
    payload = {"jobs": [{"id": 7, "title": "SAP ABAP Entwickler", "company": "ACME GmbH",
                         "location": "München, Deutschland", "snippet": "abap",
                         "link": "https://de.jooble.org/jdp/7", "updated": None}]}
    projects = REGISTRY["jooble-de"](FakeClient(payload), {"pages": 1}).fetch("SAP ABAP")
    assert projects[0].source == "jooble-de"
    assert projects[0].country == "Deutschland"
