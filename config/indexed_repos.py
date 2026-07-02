"""Registrierte Repos fuer den Server-Reindex. org ist autoritativ."""

# Kuratiert aus den echten GitHub-Repos (OuPDO + OM-Berlin). org ist autoritativ.
# Bewusst NICHT drin: die ~40 einzelnen Client-pitchpage-*-Repos (Deliverables, kaum
# Wissen, wuerden den naechtlichen Cron aufblaehen). Bei Bedarf hier ergaenzen.
INDEXED_REPOS: list[dict] = [
    # DO -- persoenlich / Wissen
    {"repo": "OuPDO/ODIN", "org": "do", "branch": "main"},
    {"repo": "OuPDO/youtube-summaries", "org": "do", "branch": "main"},
    {"repo": "OuPDO/odin-core", "org": "do", "branch": "main"},
    {"repo": "OuPDO/business-card-app", "org": "do", "branch": "main"},
    # ADO
    {"repo": "OuPDO/ado-controlling", "org": "ado", "branch": "main"},
    {"repo": "OuPDO/zoho-campaigns-stats", "org": "ado", "branch": "main"},
    {"repo": "OM-Berlin/ado-workflow-api-playground", "org": "ado", "branch": "main"},
    # OM -- Produkte / Plattform / Tooling
    {"repo": "OuPDO/OMNIPULSE", "org": "om", "branch": "main"},
    {"repo": "OuPDO/omnipulse-agent-infra", "org": "om", "branch": "main"},
    {"repo": "OuPDO/PIXPLAIN", "org": "om", "branch": "main"},
    {"repo": "OuPDO/wunschguru", "org": "om", "branch": "main"},
    {"repo": "OuPDO/om-social-wall", "org": "om", "branch": "main"},
    {"repo": "OuPDO/om-daily-briefing", "org": "om", "branch": "main"},
    {"repo": "OuPDO/stakis-scraper", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/Claude-Code-OM-Boilerplate", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/OM-Skill-Library", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/PitchPage", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/PitchPage-Boilerplate", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/pitchpage-customer-panel", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/dfs-gateway", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/EchoFlow", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/WBD-SAP-Recruiting", "org": "om", "branch": "main"},
    {"repo": "OM-Berlin/librechat-deploy", "org": "om", "branch": "main"},
]


def get_indexed_repos() -> list[dict]:
    return list(INDEXED_REPOS)
