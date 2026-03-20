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

    # n8n
    n8n_om_url: str = ""
    n8n_om_api_key: str = ""
    n8n_ado_url: str = ""
    n8n_ado_api_key: str = ""

    # ODIN
    odin_environment: str = "production"
    odin_log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.odin_allowed_users:
            return set()
        return {int(uid.strip()) for uid in self.odin_allowed_users.split(",")}


settings = Settings()
