"""ODIN Master — Central Router and Orchestrator."""

import logging
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from config.llm import get_llm

logger = logging.getLogger("odin.agents.master")

MASTER_SOUL = """Du bist ODIN, David Obladens zentrales AI Operations System.

Deine Aufgabe: Nachrichten von David entgegennehmen, die richtige Organisation
erkennen und an den zustaendigen Agent weiterleiten.

Organisationen:
- OM (ObladenMedia): Digitalagentur, Webdesign, AI-Automation, Kunden, Pipeline
- ADO (Akademie Dr. Obladen): Weiterbildung, Seminare, kommunale Entsorgung
- DO (David Obladen / DOJO): Persoenlich, Termine, Familie, Second Brain

Wenn keine Organisation klar erkennbar ist, antworte als ODIN Master direkt.

Antworte immer auf Deutsch, kurz und praezise. Kein Smalltalk.
"""

OM_SIGNALS = {
    "om", "obladen media", "obladenmedia", "agentur", "webdesign", "pipeline",
    "lead", "leads", "angebot", "angebote", "kunde", "kunden", "projekt",
    "projekte", "sofia", "carolin", "krisztina", "daniel", "simon",
    "zoho", "wordpress", "bricks", "discord", "seo", "content",
    "website", "websites", "gmail", "support@obladen",
}

ADO_SIGNALS = {
    "ado", "akademie", "dr. obladen", "seminar", "seminare", "schulung",
    "weiterbildung", "teilnehmer", "buchung", "buchungen", "entsorgung",
    "stadtreinigung", "kommunal", "hans-peter", "sebastian", "hengst",
    "christiane", "chrissi", "outlook", "akt", "akademien",
}

DO_SIGNALS = {
    "do", "dojo", "persoenlich", "privat", "termin", "termine",
    "familie", "calendar", "kalender", "erinnerung", "obsidian",
    "second brain", "notiz", "notizen",
}


class MasterState(TypedDict, total=False):
    message: str
    user_id: str
    chat_id: str
    detected_org: str
    response: str


def detect_org_fast(message: str) -> str:
    lower = message.lower()
    om_score = sum(1 for word in OM_SIGNALS if word in lower)
    ado_score = sum(1 for word in ADO_SIGNALS if word in lower)
    do_score = sum(1 for word in DO_SIGNALS if word in lower)
    if om_score > 0 and om_score >= ado_score and om_score >= do_score:
        return "om"
    if ado_score > 0 and ado_score >= om_score and ado_score >= do_score:
        return "ado"
    if do_score > 0:
        return "do"
    return ""


async def route_message(state: MasterState) -> dict:
    msg = state["message"]
    org = detect_org_fast(msg)

    if not org:
        router_llm = get_llm(role="router", max_tokens=50)
        classification = await router_llm.ainvoke([
            SystemMessage(content=(
                "Classify this message into one of: om, ado, do, master. "
                "om = ObladenMedia (agency, web, clients). "
                "ado = Akademie Dr. Obladen (seminars, education). "
                "do = David personal (calendar, family, notes). "
                "master = general/unclear. "
                "Reply with ONLY the category name, nothing else."
            )),
            HumanMessage(content=msg),
        ])
        org = classification.content.strip().lower()
        if org not in {"om", "ado", "do", "master"}:
            org = "master"

    logger.info("Routing: '%s' → %s", msg[:50], org)
    return {"detected_org": org}


async def _call_llm(state: MasterState, extra_system: str = "") -> dict:
    llm = get_llm(role="default")
    system = MASTER_SOUL + ("\n\n" + extra_system if extra_system else "")
    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=state["message"]),
    ])
    return {"response": response.content}


async def handle_om(state: MasterState) -> dict:
    return await _call_llm(state,
        "Du bist jetzt im OM-Ops Modus (ObladenMedia).\n"
        "Beantworte Fragen zu Kunden, Pipeline, Projekten, Team.\n"
        "Wenn du echte Daten brauchst (Zoho, Gmail), sage dass die "
        "n8n-Integration noch eingerichtet wird.")


async def handle_ado(state: MasterState) -> dict:
    return await _call_llm(state,
        "Du bist jetzt im ADO-Ops Modus (Akademie Dr. Obladen).\n"
        "Beantworte Fragen zu Seminaren, Teilnehmern, Buchungen.\n"
        "Wenn du echte Daten brauchst (Zoho, Outlook), sage dass die "
        "n8n-Integration noch eingerichtet wird.")


async def handle_do(state: MasterState) -> dict:
    return await _call_llm(state,
        "Du bist jetzt im DO-Personal Modus (David Obladen / DOJO).\n"
        "Beantworte Fragen zu Terminen, Erinnerungen, persoenlichen Themen.\n"
        "Wenn du Kalenderdaten brauchst, sage dass die Calendar-Integration "
        "noch eingerichtet wird.")


async def handle_master(state: MasterState) -> dict:
    return await _call_llm(state)


def route_to_org(state: MasterState) -> Literal["om", "ado", "do", "master"]:
    org = state.get("detected_org", "master")
    return org if org in {"om", "ado", "do"} else "master"


def build_master_graph() -> StateGraph:
    graph = StateGraph(MasterState)
    graph.add_node("route", route_message)
    graph.add_node("om", handle_om)
    graph.add_node("ado", handle_ado)
    graph.add_node("do", handle_do)
    graph.add_node("master", handle_master)
    graph.set_entry_point("route")
    graph.add_conditional_edges("route", route_to_org, {
        "om": "om", "ado": "ado", "do": "do", "master": "master",
    })
    graph.add_edge("om", END)
    graph.add_edge("ado", END)
    graph.add_edge("do", END)
    graph.add_edge("master", END)
    return graph


_master_graph = build_master_graph().compile()


async def process_message(message: str, user_id: str, chat_id: str) -> str:
    result = await _master_graph.ainvoke({
        "message": message,
        "user_id": user_id,
        "chat_id": chat_id,
    })
    return result.get("response", "Keine Antwort generiert.")
