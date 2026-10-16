"""MCP-Tools fuer Schreibzugriffe auf Zoho CRM und Books. Kein Tool sendet je E-Mails.

Harte Grenze (im Code erzwungen, nicht im Prompt): keine Mail-Routen, keine Erinnerungen,
keine Workflow-/Freigabe-Trigger, keine Portal-Einladungen, keine Loeschungen, keine
Rechnungen, Zahlungen oder Wiederholungen. Aufrufer koennen weder Trigger noch Besitzer
setzen (siehe integrations/zoho/write_rules.py). Rueckgabe ist immer eine Kurzfassung
(IDs, Status, Code), nie der volle Zoho-Body.
"""
from collections.abc import Callable
from typing import Any, Literal

from integrations.zoho import books_write, writes
from integrations.zoho.client import ZohoClient

Org = Literal["om", "ado"]
Module = Literal["Leads", "Contacts", "Deals", "Accounts"]
MarkStatus = Literal["sent", "declined"]


class ZohoWriteTools:
    """Schreib-Tools, gebunden an einen ZohoClient."""

    def __init__(self, client: ZohoClient) -> None:
        self._client = client

    async def crm_create(self, org: Org, module: Module, fields: dict[str, Any]) -> dict[str, Any]:
        """Datensatz in Zoho CRM anlegen (sendet nie Mails, loest keine Workflows aus).

        Liefert {ok, results:[{id, status, code}]}. Bei Duplikaten steht status error,
        code DUPLICATE_DATA und die ID des vorhandenen Datensatzes, dann nicht erneut anlegen.

        Args:
            org: om oder ado.
            module: Leads, Contacts, Deals oder Accounts.
            fields: Felder nach Zoho-API-Namen. Werte nur Text (max 5000 Zeichen), Zahl,
                Wahrheitswert, null oder Liste von Texten (max 50). Verknuepfungen (Schluessel
                endet auf _Name oder _Id) als {"id": "Ziffern"}. Besitzer, Tags, Layout,
                Erinnerungen, Trigger und Schluessel mit $ sind nicht setzbar.
        """
        return await writes.create(self._client, org, module, fields)

    async def crm_update(self, org: Org, module: Module, id: str,
                         fields: dict[str, Any]) -> dict[str, Any]:
        """Datensatz in Zoho CRM aendern (sendet nie Mails, loest keine Workflows aus).

        Liefert {ok, results:[{id, status, code}]}. Nur die genannten Felder aendern sich.

        Args:
            org: om oder ado.
            module: Leads, Contacts, Deals oder Accounts.
            id: Datensatz-ID, nur Ziffern.
            fields: Zu aendernde Felder, gleiche Regeln wie bei zoho_crm_create
                (keine Besitzer, Tags, Layout, Erinnerungen, Trigger oder $-Schluessel).
        """
        return await writes.update(self._client, org, module, id, fields)

    async def crm_add_note(self, org: Org, module: Module, id: str, title: str,
                           content: str) -> dict[str, Any]:
        """Notiz an einen Zoho-CRM-Datensatz haengen (sendet nie Mails). Liefert {ok, results}.

        Args:
            org: om oder ado.
            module: Leads, Contacts, Deals oder Accounts.
            id: Datensatz-ID, nur Ziffern.
            title: Titel, 1 bis 200 Zeichen, eine Zeile.
            content: Inhalt, 1 bis 5000 Zeichen.
        """
        return await writes.add_note(self._client, org, module, id, title, content)

    async def crm_create_task(self, org: Org, subject: str, due_date: str,
                              related_module: Module | None = None,
                              related_id: str | None = None,
                              description: str | None = None) -> dict[str, Any]:
        """Aufgabe in Zoho CRM anlegen (sendet nie Mails, setzt nie eine Erinnerung).

        Liefert {ok, results}. Eine Erinnerung waere eine Mail und existiert hier bewusst nicht.

        Args:
            org: om oder ado.
            subject: Betreff, 1 bis 255 Zeichen, eine Zeile.
            due_date: Faelligkeit als YYYY-MM-DD.
            related_module: Optional Leads, Contacts, Deals oder Accounts (nur zusammen mit related_id).
            related_id: Optional ID des verknuepften Datensatzes, nur Ziffern.
            description: Optionale Beschreibung, max 5000 Zeichen.
        """
        return await writes.create_task(self._client, org, subject, due_date, related_module,
                                        related_id, description)

    async def books_create_contact(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Kontakt in Zoho Books anlegen (nur OM, sendet nie Mails, keine Portal-Einladung).

        Liefert {ok, code, data:{contact_id, contact_name, status}}.

        Args:
            fields: Books-Felder in snake_case (z.B. contact_name, company_name,
                contact_persons als Liste mit first_name, last_name, email, phone, mobile).
                Portal-, Erinnerungs- und Versandfelder werden abgelehnt.
        """
        return await books_write.create_contact(self._client, fields)

    async def books_update_contact(self, id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Kontakt in Zoho Books aendern (nur OM, sendet nie Mails, keine Portal-Einladung).

        Args:
            id: Kontakt-ID, nur Ziffern.
            fields: Zu aendernde Felder, gleiche Regeln wie beim Anlegen.
        """
        return await books_write.update_contact(self._client, id, fields)

    async def books_create_estimate(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Angebot in Zoho Books anlegen (nur OM, Entwurf, sendet nie Mails).

        Liefert {ok, code, data:{estimate_id, estimate_number, status, total, customer_id}}.

        Args:
            fields: Z.B. customer_id, date, expiry_date, reference_number, notes, terms und
                line_items als Liste mit item_id, name, description, quantity, rate, tax_id,
                discount, unit, item_order. Versand- und Mailfelder werden abgelehnt.
        """
        return await books_write.create_estimate(self._client, fields)

    async def books_update_estimate(self, id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Angebot in Zoho Books aendern (nur OM, sendet nie Mails).

        Args:
            id: Angebots-ID, nur Ziffern.
            fields: Zu aendernde Felder, gleiche Regeln wie beim Anlegen.
        """
        return await books_write.update_estimate(self._client, id, fields)

    async def books_mark_estimate(self, id: str, status: MarkStatus) -> dict[str, Any]:
        """Angebotsstatus in Zoho Books setzen (nur OM, markiert nur, sendet nie Mails).

        Angebot annehmen bitte in Zoho Books selbst (Annahme kann dort automatisch eine
        Rechnung erzeugen und versenden, das ist hier bewusst nicht moeglich).

        Args:
            id: Angebots-ID, nur Ziffern.
            status: sent oder declined.
        """
        return await books_write.mark_estimate(self._client, id, status)

    async def books_create_item(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Artikel in Zoho Books anlegen (nur OM, sendet nie Mails).

        Liefert {ok, code, data:{item_id, name, rate, status}}.

        Args:
            fields: Z.B. name, rate, description, unit, tax_id, product_type.
        """
        return await books_write.create_item(self._client, fields)

    def crm_tools(self) -> dict[str, Callable[..., Any]]:
        return {
            "zoho_crm_create": self.crm_create, "zoho_crm_update": self.crm_update,
            "zoho_crm_add_note": self.crm_add_note,
            "zoho_crm_create_task": self.crm_create_task,
        }

    def books_tools(self) -> dict[str, Callable[..., Any]]:
        return {
            "zoho_books_create_contact": self.books_create_contact,
            "zoho_books_update_contact": self.books_update_contact,
            "zoho_books_create_estimate": self.books_create_estimate,
            "zoho_books_update_estimate": self.books_update_estimate,
            "zoho_books_mark_estimate": self.books_mark_estimate,
            "zoho_books_create_item": self.books_create_item,
        }
