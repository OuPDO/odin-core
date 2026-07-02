from memory.postgres import get_supabase

TABLE = "odin_project_registry"

_COLUMNS = {"id", "name", "org", "path", "git_remote", "stack", "status",
            "purpose_oneliner", "last_commit_at", "last_scanned_at", "size_tier"}


def upsert_project(row: dict) -> None:
    clean = {k: v for k, v in row.items() if k in _COLUMNS}
    get_supabase().table(TABLE).upsert(clean, on_conflict="path").execute()

def query_projects(org: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
    q = get_supabase().table(TABLE).select("*")
    if org:
        q = q.eq("org", org)
    if status:
        q = q.eq("status", status)
    return q.limit(limit).execute().data
