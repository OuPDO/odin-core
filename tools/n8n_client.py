"""n8n Webhook Client — calls n8n workflows on OM/ADO servers."""

import logging

import httpx

from config import settings

logger = logging.getLogger("odin.tools.n8n")


async def call_n8n_webhook(
    org: str,
    workflow: str,
    payload: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    """Call an n8n webhook endpoint.

    Args:
        org: Organization ("om" or "ado")
        workflow: Workflow identifier (e.g., "zoho-pipeline", "gmail-inbox")
        payload: Optional JSON payload to send
        timeout: Request timeout in seconds

    Returns:
        JSON response from n8n workflow
    """
    if org == "om":
        base_url = settings.n8n_om_url
        api_key = settings.n8n_om_api_key
    elif org == "ado":
        base_url = settings.n8n_ado_url
        api_key = settings.n8n_ado_api_key
    else:
        raise ValueError(f"Unbekannte Organisation: {org}")

    if not base_url:
        return {"error": f"n8n URL fuer {org} nicht konfiguriert"}

    url = f"{base_url}/webhook/{workflow}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                url,
                json=payload or {},
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("n8n %s/%s HTTP %s: %s", org, workflow, e.response.status_code, e)
            return {"error": f"n8n Fehler: HTTP {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error("n8n %s/%s Verbindungsfehler: %s", org, workflow, e)
            return {"error": f"n8n nicht erreichbar: {e}"}
