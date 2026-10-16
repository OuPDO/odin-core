"""Zoho configuration from the environment. EU hosts only (v1)."""
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from integrations.zoho.validation import is_id

log = logging.getLogger("odin.integrations.zoho")

ACCOUNTS_URL = "https://accounts.zoho.eu"
API_DOMAIN = "https://www.zohoapis.eu"
BOOKS_ORG = "om"
ORGS = ("om", "ado")


@dataclass(frozen=True)
class OrgCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _host(raw: str, expected: str, name: str) -> str:
    """The configured value must be exactly the EU host (one trailing slash tolerated)."""
    if raw.strip().rstrip("/") != expected:
        raise ValueError(f"{name} must be exactly {expected}")
    return expected


@dataclass(frozen=True)
class ZohoConfig:
    accounts_url: str
    api_domain: str
    orgs: Mapping[str, OrgCredentials]
    books_organization_id: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ZohoConfig":
        orgs = {}
        for org in ORGS:
            prefix = f"ZOHO_{org.upper()}_"
            values = [env.get(prefix + key, "").strip()
                      for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN")]
            if all(values):
                orgs[org] = OrgCredentials(*values)
            elif any(values):
                log.warning("Zoho org %s has incomplete credentials; org disabled", org)
        return cls(
            accounts_url=_host(env.get("ZOHO_ACCOUNTS_URL") or ACCOUNTS_URL, ACCOUNTS_URL,
                               "ZOHO_ACCOUNTS_URL"),
            api_domain=_host(env.get("ZOHO_API_DOMAIN") or API_DOMAIN, API_DOMAIN,
                             "ZOHO_API_DOMAIN"),
            orgs=orgs,
            books_organization_id=_books_org_id(env),
        )

    @property
    def crm_orgs(self) -> tuple[str, ...]:
        return tuple(org for org in ORGS if org in self.orgs)

    @property
    def books_enabled(self) -> bool:
        return BOOKS_ORG in self.orgs and self.books_organization_id is not None


def _books_org_id(env: Mapping[str, str]) -> str | None:
    raw = env.get("ZOHO_BOOKS_ORGANIZATION_ID", "").strip()
    if not raw:
        return None
    if not is_id(raw):
        log.warning("ZOHO_BOOKS_ORGANIZATION_ID is set but not 1 to 25 digits; Books disabled")
        return None
    return raw
