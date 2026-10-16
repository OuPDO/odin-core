"""MCP-Tools fuer Zoho CRM und Books. Nur lesend, keine Schreib-Operationen.

Die Tools werden nur fuer Orgs registriert, deren Credentials vollstaendig in der
Umgebung stehen (ZOHO_OM_*, ZOHO_ADO_*; Books zusaetzlich ZOHO_BOOKS_ORGANIZATION_ID
und OM-Credentials). Ohne Credentials entstehen keine Zoho-Tools.

Datensparsamkeit: Die Daten gehen an einen externen Agenten (Hermes). CRM-Abfragen holen
standardmaessig nur eine feste Feldliste je Modul, Books-Objekte werden auf eine feste
Schluesselliste gekuerzt (Details je Tool).
"""
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from integrations.zoho import books, crm
from integrations.zoho.client import ZohoClient
from integrations.zoho.config import ZohoConfig

log = logging.getLogger("odin.mcp.zoho")

Org = Literal["om", "ado"]
Module = Literal["Leads", "Contacts", "Deals", "Accounts"]
SearchBy = Literal["word", "email", "phone", "full_name", "company", "account", "name"]
EstimateStatus = Literal["draft", "sent", "invoiced", "accepted", "declined", "expired"]


class ZohoTools:
    """Tool-Funktionen, gebunden an einen ZohoClient (Registrierung via register_zoho_tools)."""

    def __init__(self, client: ZohoClient) -> None:
        self._client = client

    async def crm_search(self, org: Org, module: Module, by: SearchBy, value: str,
                         page: int = 1) -> dict[str, Any]:
        """Zoho CRM durchsuchen (nur lesend, aendert nichts).

        Liefert {data, page, more_records}. Ohne Treffer ist data leer. Treffer enthalten nur
        die Standardfelder des Moduls (Namen, Firma, E-Mail, Telefon, Status, Besitzername,
        Zeitstempel; bei Deals Phase, Betrag, Abschlussdatum), keine Beschreibungen oder Adressen.

        Args:
            org: om oder ado (nur Orgs mit konfigurierten Credentials).
            module: Leads, Contacts, Deals oder Accounts.
            by: word, email oder phone (Freitext-Suche) oder ein Feld je Modul:
                Leads full_name/company, Contacts full_name/account, Accounts name, Deals name.
            value: Suchwert, 2 bis 200 Zeichen.
            page: Seite 1 bis 40 (50 Treffer pro Seite).
        """
        return await crm.search(self._client, org, module, by, value, page)

    async def crm_get(self, org: Org, module: Module, id: str,
                      fields: list[str] | None = None) -> dict[str, Any]:
        """Einen Zoho-CRM-Datensatz per ID holen (nur lesend). Liefert {data}.

        Ohne fields kommen nur die Standardfelder des Moduls (Datensparsamkeit). Weitere
        Felder nur gezielt per fields anfordern.

        Args:
            org: om oder ado.
            module: Leads, Contacts, Deals oder Accounts.
            id: Datensatz-ID, nur Ziffern.
            fields: Optional Feld-API-Namen (max. 50, nur Buchstaben, Ziffern, Unterstrich).
        """
        return await crm.get_record(self._client, org, module, id, fields)

    async def crm_list(self, org: Org, module: Module, fields: list[str] | None = None,
                       page: int = 1) -> dict[str, Any]:
        """Datensaetze eines Zoho-CRM-Moduls auflisten (nur lesend).

        Liefert {data, page, more_records}.

        Args:
            org: om oder ado.
            module: Leads, Contacts, Deals oder Accounts.
            fields: Feld-API-Namen (max. 50); leer = Standardfelder des Moduls (Datensparsamkeit).
            page: Seite 1 bis 40 (50 Datensaetze pro Seite).
        """
        return await crm.list_records(self._client, org, module, fields, page)

    async def crm_list_notes(self, org: Org, module: Module, id: str,
                             page: int = 1) -> dict[str, Any]:
        """Notizen eines Zoho-CRM-Datensatzes lesen (nur lesend). Liefert {data, page, more_records}.

        Pro Notiz nur Titel, Inhalt, Zeitstempel und Besitzername.

        Args:
            org: om oder ado.
            module: Leads, Contacts, Deals oder Accounts.
            id: Datensatz-ID, nur Ziffern.
            page: Seite 1 bis 40.
        """
        return await crm.list_notes(self._client, org, module, id, page)

    async def books_search_contacts(self, text: str, page: int = 1) -> dict[str, Any]:
        """Zoho-Books-Kontakte suchen (nur OM, nur lesend). Liefert {data, page, has_more_page}.

        Pro Kontakt nur: contact_id, contact_name, company_name, email, phone, status,
        outstanding_receivable_amount, currency_code.

        Args:
            text: Suchtext, 1 bis 100 Zeichen.
            page: Seite 1 bis 100 (50 Treffer pro Seite).
        """
        return await books.search_contacts(self._client, text, page)

    async def books_get_contact(self, id: str) -> dict[str, Any]:
        """Einen Zoho-Books-Kontakt per ID holen (nur OM, nur lesend). Liefert {data}.

        Gleiche Schluesselliste wie bei der Kontaktsuche (keine Adressen, Ansprechpartner, Notizen).

        Args:
            id: Kontakt-ID, nur Ziffern.
        """
        return await books.get_contact(self._client, id)

    async def books_list_estimates(self, status: EstimateStatus | None = None,
                                   customer_id: str | None = None,
                                   page: int = 1) -> dict[str, Any]:
        """Zoho-Books-Angebote auflisten (nur OM, nur lesend). Liefert {data, page, has_more_page}.

        Pro Angebot nur: estimate_id, estimate_number, date, expiry_date, status, customer_id,
        customer_name, total, currency_code, reference_number; Positionen nur mit name,
        description, quantity, rate, item_total.

        Args:
            status: Optionaler Statusfilter: draft, sent, invoiced, accepted, declined, expired.
            customer_id: Optionale Kunden-ID (Ziffern).
            page: Seite 1 bis 100 (50 Treffer pro Seite).
        """
        return await books.list_estimates(self._client, status, customer_id, page)

    async def books_get_estimate(self, id: str) -> dict[str, Any]:
        """Ein Zoho-Books-Angebot per ID holen (nur OM, nur lesend). Liefert {data}.

        Gleiche Schluesselliste wie bei der Angebotsliste, inklusive gekuerzter Positionen.

        Args:
            id: Angebots-ID, nur Ziffern.
        """
        return await books.get_estimate(self._client, id)

    def crm_tools(self) -> dict[str, Callable[..., Any]]:
        return {
            "zoho_crm_search": self.crm_search, "zoho_crm_get": self.crm_get,
            "zoho_crm_list": self.crm_list, "zoho_crm_list_notes": self.crm_list_notes,
        }

    def books_tools(self) -> dict[str, Callable[..., Any]]:
        return {
            "zoho_books_search_contacts": self.books_search_contacts,
            "zoho_books_get_contact": self.books_get_contact,
            "zoho_books_list_estimates": self.books_list_estimates,
            "zoho_books_get_estimate": self.books_get_estimate,
        }


def register_zoho_tools(mcp: FastMCP, env: Mapping[str, str] | None = None) -> list[str]:
    """Registriert die Zoho-Lese-Tools nach vorhandenen Credentials. Gibt die Tool-Namen zurueck.

    Ungueltige Konfiguration (z.B. fremder Host) registriert nichts und wirft nicht,
    damit odin-core trotzdem startet. Die Warnung nennt nie den konfigurierten Wert.
    """
    try:
        config = ZohoConfig.from_env(os.environ if env is None else env)
    except ValueError as exc:
        log.warning("Zoho-Tools nicht registriert: %s", exc)
        return []
    if not config.crm_orgs:
        return []
    tools = ZohoTools(ZohoClient(config))
    selected = dict(tools.crm_tools())
    if config.books_enabled:
        selected.update(tools.books_tools())
    for name, fn in selected.items():
        mcp.tool(name=name)(fn)
    log.info("Zoho-Tools registriert: orgs=%s books=%s", ",".join(config.crm_orgs),
             config.books_enabled)
    return list(selected)
