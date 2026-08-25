"""Upwork GraphQL adapter - marketplace is ilanlari.

Upwork'un TUM public yollari kapali (2026-08-17 testi):
    /ab/feed/jobs/rss          -> HTTP 410 (RSS 2024'te kaldirildi)
    /api/profiles/v2/...json   -> HTTP 410
    /nx/search/jobs/?q=...     -> HTTP 403 (Cloudflare)
    api.upwork.com/graphql     -> HTTP 401 (token gerekiyor)

Yani bu kaynak ancak OAuth2 access token ile calisir:
  1. https://www.upwork.com/developer/keys/apply -> uygulama olustur (Client ID + Secret)
  2. .env icine UPWORK_CLIENT_ID / UPWORK_CLIENT_SECRET yaz
  3. Bir kez `py -m scanner upwork-auth` calistir; token alinir ve `upwork_auth`
     tarafindan suresi dolunca kendiliginden yenilenir.

Kimlik bilgisi yoksa kaynak hata dondurur, tarama diger kaynaklarla devam eder.
"""
from __future__ import annotations

import logging
import os

from ..models import Project
from ..normalize import clean_text, detect_contract, detect_work_mode, parse_date, strip_html
from . import upwork_auth
from .base import BaseSource, BlockedError, EnvVar, FetchError

log = logging.getLogger(__name__)

ENDPOINT = "https://api.upwork.com/graphql"
JOB_URL = "https://www.upwork.com/jobs/{ciphertext}"

QUERY = """
query jobSearch($filter: MarketplaceJobPostingsSearchFilter,
                $sort: [MarketplaceJobPostingSearchSortAttribute]) {
  marketplaceJobPostingsSearch(marketPlaceJobFilter: $filter, sortAttributes: $sort) {
    totalCount
    edges {
      node {
        id
        title
        description
        ciphertext
        duration
        engagement
        experienceLevel
        createdDateTime
        publishedDateTime
        totalApplicants
        preferredFreelancerLocation
        amount { rawValue currency }
        hourlyBudgetMin { rawValue }
        hourlyBudgetMax { rawValue }
        skills { name }
        client { location { country } }
        job { contractTerms { contractType } }
      }
    }
  }
}
"""


class UpworkSource(BaseSource):
    name = "upwork"
    requires_env = "UPWORK_CLIENT_ID"
    title = "Upwork"
    signup_url = "https://www.upwork.com/developer/keys/apply"
    notes = ("Public yollar kapali; sadece OAuth2 anahtariyla calisir. Anahtar degisince "
             "'py -m scanner upwork-auth' ile token yeniden alinir.")
    env_vars = (EnvVar("UPWORK_CLIENT_ID", "Client ID"),
                EnvVar("UPWORK_CLIENT_SECRET", "Client Secret"),
                EnvVar("UPWORK_REDIRECT_URI", "Redirect URI", required=False, secret=False),
                EnvVar("UPWORK_TENANT_ID", "Tenant ID", required=False, secret=False))

    def fetch(self, query: str) -> list[Project]:
        page_size = int(self.options.get("page_size", 50))
        pages = int(self.options.get("pages", 2))

        projects: list[Project] = []
        for page in range(pages):
            variables = {
                "filter": {
                    "searchExpression_eq": query or "SAP ABAP",
                    "pagination_eq": {"after": str(page * page_size), "first": page_size},
                },
                "sort": [{"field": "RECENCY", "direction": "DESC"}],
            }
            data = self._graphql({"query": QUERY, "variables": variables})

            search = ((data.get("data") or {}).get("marketplaceJobPostingsSearch")) or {}
            edges = search.get("edges") or []
            if not edges:
                break
            projects.extend(self._to_project(edge.get("node") or {}) for edge in edges)
            if len(edges) < page_size:
                break
        return projects

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        tenant = os.environ.get("UPWORK_TENANT_ID", "").strip()
        if tenant:
            # organizasyon baglami isteyen anahtarlar icin
            headers["X-Upwork-API-TenantId"] = tenant
        return headers

    def _graphql(self, payload: dict) -> dict:
        """GraphQL istegi; token doldiyse bir kez yenileyip tekrar dener."""
        token = upwork_auth.get_token()
        try:
            data = self.client.post(ENDPOINT, json=payload, headers=self._headers(token)).json()
        except BlockedError:
            # 401: token suresi dolmus olabilir - bir kez yenile, tekrar dene
            log.info("upwork 401 verdi, token yenileniyor")
            token = upwork_auth.get_token(force_refresh=True)
            data = self.client.post(ENDPOINT, json=payload, headers=self._headers(token)).json()

        if data.get("errors"):
            raise FetchError(f"Upwork GraphQL hatasi: {data['errors'][:1]}")
        return data

    def _to_project(self, node: dict) -> Project:
        title = clean_text(node.get("title"))
        description = strip_html(node.get("description"))[:4000]
        skills = [clean_text(s.get("name")) for s in (node.get("skills") or []) if s.get("name")]
        country = clean_text((((node.get("client") or {}).get("location")) or {}).get("country"))
        contract_type = clean_text((((node.get("job") or {}).get("contractTerms")) or {})
                                   .get("contractType"))
        ciphertext = clean_text(node.get("ciphertext")).lstrip("~")

        return Project(
            source=self.name,
            source_id=str(node.get("id") or ciphertext),
            url=JOB_URL.format(ciphertext=ciphertext) if ciphertext else "https://www.upwork.com/",
            title=title,
            company="",                       # Upwork isveren adini vermiyor
            location=clean_text(node.get("preferredFreelancerLocation")) or country,
            country=country,
            # Upwork'un tamami uzaktan calisilan bir pazar yeri
            work_mode=detect_work_mode(title, description) or "remote",
            engagement=clean_text(node.get("engagement")) or contract_type,
            is_contract=detect_contract(title, description, contract_type) if description else True,
            duration=clean_text(node.get("duration")),
            budget_raw=self._budget(node),
            currency=clean_text(((node.get("amount") or {}).get("currency"))),
            description=description,
            skills=skills,
            posted_at=parse_date(node.get("publishedDateTime") or node.get("createdDateTime")),
        )

    @staticmethod
    def _budget(node: dict) -> str:
        """Sabit fiyat veya saatlik aralik."""
        currency = clean_text(((node.get("amount") or {}).get("currency"))) or "USD"
        low = ((node.get("hourlyBudgetMin") or {}).get("rawValue"))
        high = ((node.get("hourlyBudgetMax") or {}).get("rawValue"))
        if low or high:
            if low and high:
                return f"{float(low):.0f}-{float(high):.0f} {currency}/saat"
            value = low or high
            return f"{float(value):.0f} {currency}/saat"
        fixed = (node.get("amount") or {}).get("rawValue")
        return f"{float(fixed):.0f} {currency}" if fixed else ""
