from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    environment: str = 'local'
    log_file: str = 'runtime/logs/mediagent.jsonl'
    log_show_chunks: bool = True
    log_show_query: bool = True
    database_url: str = 'sqlite:///runtime/mediagent.db'
    hospital_name: str = 'CMC Vellore'
    hospital_id: str = 'cmc-vellore'
    hospital_portal_url: str = ''
    public_url: str = 'http://localhost:8000'
    admin_token: str = ''
    supabase_url: str = ''
    supabase_anon_key: str = ''
    gemini_api_key: str = ''
    gemini_model: str = ''
    gemini_daily_limit: int = 100
    ai_provider: str = 'auto'
    ai_api_key: str = ''
    ai_api_keys: str = ''
    gemini_api_keys: str = ''
    openrouter_api_keys: str = ''
    ai_pool_rpm: int = Field(default=60, ge=1)
    ai_pool_concurrency: int = Field(default=2, ge=1)
    ai_pool_max_attempts: int = Field(default=3, ge=1, le=10)
    ai_pool_shared_limits: bool = False
    ai_model: str = ''
    ai_base_url: str = 'https://openrouter.ai/api/v1'
    ai_daily_limit: int = 100
    openrouter_api_key: str = ''
    openrouter_model: str = ''
    groq_api_key: str = ''
    groq_api_keys: str = ''
    groq_model: str = ''
    qdrant_url: str = ''
    qdrant_path: str = ''
    qdrant_api_key: str = ''
    qdrant_collection: str = 'mediagent'
    hybrid_enabled: bool = False
    rerank_enabled: bool = False
    telegram_bot_token: str = ''
    telegram_webhook_secret: str = ''
    channel_hash_secret: str = ''
    whatsapp_enabled: bool = False
    whatsapp_access_token: str = ''
    whatsapp_phone_id: str = ''
    whatsapp_app_secret: str = ''
    whatsapp_verify_token: str = ''
    whatsapp_graph_version: str = ''
    retention_hours: int = 24
    source_max_age_days: int = 90

    @model_validator(mode='after')
    def cloud_requirements(self):
        if self.ai_provider.strip().lower() not in {'pool', 'auto', 'local', 'gemini', 'groq', 'openrouter', 'openai_compatible'}:
            raise ValueError('AI_PROVIDER must be pool, auto, local, gemini, groq, openrouter, or openai_compatible')
        if self.ai_base_url:
            parsed_ai = urlsplit(self.ai_base_url)
            if parsed_ai.scheme != 'https' or not parsed_ai.hostname or parsed_ai.username or parsed_ai.password:
                raise ValueError('AI_BASE_URL must be an HTTPS URL without credentials')
        for name in ('public_url', 'supabase_url', 'hospital_portal_url'):
            value = getattr(self, name)
            if not value:
                continue
            parsed = urlsplit(value)
            allowed_schemes = {'http', 'https'} if name == 'public_url' else {'https'}
            if parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f'{name} must be an absolute URL without credentials')
            if name != 'hospital_portal_url' and (parsed.path not in ('', '/') or parsed.query or parsed.fragment):
                raise ValueError(f'{name} must contain only the URL origin')
            setattr(self, name, value.rstrip('/'))
        if self.environment not in {'local', 'cloud', 'test'}:
            raise ValueError('ENVIRONMENT must be local, cloud, or test')
        if self.environment == 'cloud':
            if not self.database_url.startswith('postgresql+psycopg://'):
                raise ValueError('Cloud requires persistent PostgreSQL via psycopg')
            if not self.public_url.startswith('https://'):
                raise ValueError('Cloud requires an HTTPS PUBLIC_URL')
            if not self.supabase_url or not self.supabase_anon_key:
                raise ValueError('Cloud staff authentication requires Supabase')
        if self.telegram_bot_token and (not self.telegram_webhook_secret or len(self.channel_hash_secret) < 32):
            raise ValueError('Telegram requires a webhook secret and a 32+ character channel hash secret')
        if self.whatsapp_enabled and not all((self.whatsapp_access_token, self.whatsapp_phone_id,
                                             self.whatsapp_app_secret, self.whatsapp_verify_token,
                                             self.whatsapp_graph_version, len(self.channel_hash_secret) >= 32)):
            raise ValueError('WhatsApp configuration is incomplete')
        if self.hybrid_enabled and not (self.qdrant_url or self.qdrant_path):
            raise ValueError('Hybrid retrieval requires QDRANT_URL or QDRANT_PATH')
        if not 1 <= self.retention_hours <= 168:
            raise ValueError('Retention must be between 1 and 168 hours')
        return self

    @property
    def portal_url(self):
        if self.hospital_portal_url:
            return self.hospital_portal_url
        return 'https://www.cmcvellore.ac.in/patient-portal/' if self.hospital_id == 'cmc-vellore' else ''
