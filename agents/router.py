import logging

from config.llm import get_azure_chat

logger = logging.getLogger("odin.agents.router")

INTENTS = {"knowledge", "om", "ado", "do", "chat"}

_PROMPT = """Klassifiziere die Nachricht in genau EIN Label: knowledge, om, ado, do, chat.
Beispiele:
"welche projekte laufen gerade bei om" -> knowledge
"was habe ich zum thema datev gemacht" -> knowledge
"schick eine rechnung an kunde x" -> om
"moin" -> chat

Regeln:
- knowledge: Frage nach Davids Projekten/Wissen/Notizen (Status, Recall).
- om/ado/do: konkrete Aktion fuer die Organisation.
- chat: Begruessung/Smalltalk.
Antworte NUR mit dem Label.

Nachricht: {msg}
Label:"""


def classify_intent(message: str) -> str:
    try:
        resp = get_azure_chat().invoke(_PROMPT.format(msg=message)).content.strip().lower()
        for label in INTENTS:
            if resp.startswith(label):
                return label
        return "chat"
    except Exception:
        logger.warning("classify_intent failed, falling back to 'chat'", exc_info=True)
        return "chat"
