from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str
    telegram_webhook_secret: str = ""
    odin_allowed_users: str = ""  # comma-separated Telegram user IDs

    # LLM Provider (openrouter, anthropic, azure, vertex)
    llm_provider: str = "openrouter"
    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model_router: str = "anthropic/claude-haiku-4-5-20251001"
    llm_model_default: str = "anthropic/claude-sonnet-4-20250514"
    llm_model_analysis: str = "anthropic/claude-opus-4-20250514"

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    database_url: str = ""

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # Azure OpenAI (EU / Sweden Central)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_embedding_deployment: str = "text-embedding-3-small"
    azure_embedding_model: str = "text-embedding-3-small"
    azure_embedding_api_version: str = "2024-02-01"
    azure_embedding_dim: int = 1536
    azure_chat_deployment: str = "gpt-5-mini"
    azure_chat_model: str = "gpt-5-mini"
    azure_chat_api_version: str = "2024-12-01-preview"
    azure_transcribe_deployment: str = "whisper"
    azure_transcribe_api_version: str = "2024-06-01"
    # Embedding-Retry (429-Fix). Azure S0-Tier drosselt grosse Bursts per HTTP
    # 429 mit "retry after 60s". Der alte 1/2/4s-Backoff gab vor dem Reset auf
    # -> Reindex embeddete 0 und der Backlog drainierte nie. Geduldige Retries
    # (Backoff bis 120s) sind selbst der Throttle: sie spacen die Requests aus
    # und verhindern so den Storm. Kein Per-Run-Cap noetig.
    embed_client_max_retries: int = 6       # Azure-SDK-Retries (respektiert Retry-After-Header)
    embed_max_attempts: int = 5             # aeussere Retry-Schleife pro Batch
    embed_retry_base_seconds: float = 15.0  # Backoff-Basis, cap 120s -- matcht Azure-60s-Fenster
    # Proaktive Drossel (429-Fix, Teil 2). Der Backoff oben greift nur REAKTIV
    # nach einem 429. Zwei Bursts reissen das S0-Limit trotzdem: (a) eine grosse
    # Datei ging als EIN Riesen-Request raus (~226 Chunks/~45K Tokens -> Per-
    # Request-TPM-Limit sofort gerissen, jeder Retry schickt denselben Brocken),
    # (b) viele Files feuerten back-to-back (RPM-Burst). Kleine Sub-Batches halten
    # die Tokens/Request klein, das Mindestintervall haelt die Requests/s unter dem
    # RPM-Limit. 0 = Drossel aus (Backoff bleibt).
    embed_batch_size: int = 16              # Chunks pro Embed-Request (Sub-Batch)
    embed_min_interval_seconds: float = 3.0  # Mindestabstand zwischen zwei Requests
    # Qdrant-Upsert-Batching. Nachdem die Embed-Drossel den 429 beseitigt hatte,
    # trat der naechste monolithische Request zutage: eine 2.4-MB-Datei (~3400
    # Chunks) ging als EIN Upsert-PUT raus -> Payload sprengte die Body-Grenze des
    # Proxy vor Qdrant -> 502. Points werden daher in Batches geschrieben; bei
    # Teilfehler rollt die Quelle zurueck (kein inkonsistenter content_hash).
    # 16 Points x 1536-dim-Float-Vektor (REST/JSON) ~= 0.4 MB/PUT -- sicher unter
    # der ueblichen 1-MB-Proxy-Body-Grenze (64 waeren ~1.5-2 MB und koennten den
    # 502 reproduzieren). Env-ueberschreibbar, falls das echte Limit hoeher liegt.
    upsert_batch_size: int = 16             # Points pro Qdrant-Upsert-PUT
    upsert_max_attempts: int = 3            # Retry fuer transiente Qdrant-5xx + Rollback

    # n8n
    n8n_om_url: str = ""
    n8n_om_api_key: str = ""
    n8n_ado_url: str = ""
    n8n_ado_api_key: str = ""

    # ODIN
    odin_environment: str = "production"
    odin_log_level: str = "INFO"
    # Telegram-Bot im odin-core-Prozess. Standard aus, weil Hermes den
    # @do_odin_bot-Token via Webhook besitzt (D-022) -> kein Polling-Konflikt.
    odin_telegram_enabled: bool = False

    # Remote-HTTP-MCP (SP-4.4). Leer = Endpoint fail-closed (401 fuer alle).
    odin_mcp_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.odin_allowed_users:
            return set()
        return {int(uid.strip()) for uid in self.odin_allowed_users.split(",")}


settings = Settings()
