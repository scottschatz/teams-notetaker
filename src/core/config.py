"""
Configuration management for Teams Meeting Transcript Summarizer.

Loads configuration from:
1. .env file (secrets - never committed)
2. config.yaml (runtime settings - editable via dashboard)
"""

from dataclasses import dataclass, field
from typing import List, Optional
import os
import yaml
from dotenv import load_dotenv
from pathlib import Path


@dataclass
class GraphAPIConfig:
    """Microsoft Graph API configuration."""

    client_id: str
    client_secret: str
    tenant_id: str
    authority: str
    scopes: List[str] = field(
        default_factory=lambda: [
            "https://graph.microsoft.com/.default"  # Application permissions
        ]
    )

    def __post_init__(self):
        """Build authority URL from tenant ID if not provided."""
        if self.tenant_id and not self.authority:
            self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"


@dataclass
class DatabaseConfig:
    """PostgreSQL database configuration."""

    host: str = "localhost"
    port: int = 5432
    database: str = "teams_notetaker"
    user: str = "postgres"
    password: str = ""

    @property
    def connection_string(self) -> str:
        """Generate PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class ClaudeConfig:
    """
    Anthropic Claude API configuration.

    Note: Claude Haiku is now the FALLBACK model. The primary model is Gemini 3 Flash.
    See src/ai/summarizer.py for the model hierarchy:
    - Primary: Gemini 3 Flash (requires GOOGLE_API_KEY env var)
    - Fallback: Claude Haiku 4.5 (this config)

    If GOOGLE_API_KEY is not set, Claude Haiku is used exclusively.
    """

    api_key: str
    model: str = "claude-haiku-4-5"
    max_tokens: int = 2000
    temperature: float = 0.7


@dataclass
class AzureADConfig:
    """Azure AD SSO configuration for web dashboard."""

    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""
    redirect_uri: str = "http://localhost:8000/auth/callback"
    allowed_domains: str = "townsquaremedia.com"  # Comma-separated list of allowed domains
    authority: str = ""

    def __post_init__(self):
        """Build authority URL from tenant ID if not provided."""
        if self.tenant_id and not self.authority:
            self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

    def get_allowed_domains_list(self) -> list:
        """Get list of allowed domains from comma-separated config."""
        if not self.allowed_domains:
            return []
        return [d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()]

    def is_whitelist_enabled(self) -> bool:
        """Check if user whitelist is enabled."""
        # For now, just domain validation
        # Can be extended to check specific allowed users from env
        return False


@dataclass
class AzureRelayConfig:
    """Azure Relay configuration for webhook listening."""

    namespace: str = ""  # e.g., "myrelay.servicebus.windows.net"
    hybrid_connection: str = "teams-webhooks"
    key_name: str = "RootManageSharedAccessKey"
    key: str = ""

    def is_configured(self) -> bool:
        """Check if Azure Relay is properly configured."""
        return bool(self.namespace and self.key)

    @property
    def webhook_url(self) -> str:
        """
        Get the public webhook URL for Graph API subscriptions.

        Note: This is the HTTP endpoint format for external services (like Microsoft Graph)
        to send requests TO the Azure Relay. This is different from the WebSocket URL
        (wss://.../$hc/...) that the listener uses to connect.
        """
        if not self.is_configured():
            return ""
        return f"https://{self.namespace}/{self.hybrid_connection}"


@dataclass
class AppConfig:
    """Runtime application configuration (from config.yaml)."""

    # Polling configuration
    polling_interval_minutes: int = 5
    lookback_hours: int = 48

    # Operating mode
    pilot_mode_enabled: bool = True

    # Job processing
    max_concurrent_jobs: int = 5
    job_timeout_minutes: int = 10

    # AI summarization
    summary_max_tokens: int = 2000

    # Distribution
    email_enabled: bool = True
    email_from: str = "noreply@townsquaremedia.com"
    teams_chat_enabled: bool = True
    debug_mode: bool = False  # Debug mode: only send to specific test recipients
    debug_email_recipients: list = None  # List of emails to send to in debug mode

    # Filtering
    minimum_meeting_duration_minutes: int = 5

    # Worker configuration
    worker_heartbeat_interval_seconds: int = 30

    # Inbox Monitoring Settings (v3.1)
    inbox_check_interval_seconds: int = 60  # How often to check for commands
    inbox_lookback_minutes: int = 60  # How far back to look for messages
    inbox_delete_processed_commands: bool = True  # Delete subscribe/unsubscribe after processing
    inbox_keep_feedback: bool = True  # Keep feedback emails in inbox

    # Alerting Settings (v3.1)
    alert_email_enabled: bool = True  # Send email alerts for critical issues
    alert_email_recipients: list = None  # Admin email(s) for alerts

    # Chat Command Settings (v2.0) - DEPRECATED, use inbox monitoring
    chat_monitoring_enabled: bool = True
    chat_check_interval_minutes: int = 2
    chat_lookback_days: int = 7

    # Email Preference Defaults (v2.0)
    default_email_preference: bool = True
    allow_chat_preferences: bool = True

    # Enhanced Summary Settings (v2.0)
    enable_action_items: bool = True
    enable_decisions: bool = True
    enable_topic_segmentation: bool = True
    enable_highlights: bool = True
    enable_mentions: bool = True
    max_highlights: int = 5

    # Summarization Approach (v2.1)
    use_single_call_summarization: bool = True  # Default: use single-call (faster, cheaper, better quality)

    # SharePoint Links (v2.0)
    use_sharepoint_links: bool = True
    sharepoint_link_expiration_days: int = 90

    # Webhooks (v3.0) - Azure Relay for org-wide discovery
    webhooks_enabled: bool = True  # Enable webhook-based discovery
    webhook_backfill_hours: int = 4  # Maximum hours to backfill on startup (usually uses gap detection)
    webhook_safety_net_enabled: bool = True  # Daily catchup for missed meetings

    # User Preferences (v3.0) - Opt-in system
    default_email_preference: bool = False  # Default opt-out (users must opt-in)


class ConfigManager:
    """Central configuration manager.

    Loads configuration from:
    - .env file for secrets (Graph API, Claude API, DB credentials)
    - config.yaml for runtime settings (editable via dashboard)
    """

    def __init__(self, env_file: Optional[str] = None, config_file: Optional[str] = None):
        """
        Initialize configuration manager.

        Args:
            env_file: Path to .env file (default: .env in project root)
            config_file: Path to config.yaml file (default: config.yaml in project root)
        """
        # Load environment variables
        if env_file is None:
            env_file = ".env"
        load_dotenv(env_file)

        # Set config file path
        if config_file is None:
            config_file = "config.yaml"
        self.config_file = config_file

        # Load configurations
        self._load_env_config()
        self._load_yaml_config()

    def _load_env_config(self):
        """Load secrets from .env file."""

        # Graph API configuration
        self.graph_api = GraphAPIConfig(
            client_id=os.getenv("GRAPH_CLIENT_ID", ""),
            client_secret=os.getenv("GRAPH_CLIENT_SECRET", ""),
            tenant_id=os.getenv("GRAPH_TENANT_ID", ""),
            authority=os.getenv("GRAPH_AUTHORITY", ""),
        )

        # Database configuration
        self.database = DatabaseConfig(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "teams_notetaker"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", ""),
        )

        # Claude API configuration
        self.claude = ClaudeConfig(
            api_key=os.getenv("CLAUDE_API_KEY", ""),
            model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5"),
            max_tokens=int(os.getenv("CLAUDE_MAX_TOKENS", "2000")),
            temperature=float(os.getenv("CLAUDE_TEMPERATURE", "0.7")),
        )

        # Azure AD SSO configuration
        self.azure_ad = AzureADConfig(
            enabled=os.getenv("AZURE_AD_ENABLED", "false").lower() == "true",
            client_id=os.getenv("AZURE_AD_CLIENT_ID", ""),
            client_secret=os.getenv("AZURE_AD_CLIENT_SECRET", ""),
            tenant_id=os.getenv("AZURE_AD_TENANT_ID", ""),
            redirect_uri=os.getenv("AZURE_AD_REDIRECT_URI", "http://localhost:8000/auth/callback"),
            allowed_domains=os.getenv("AZURE_AD_ALLOWED_DOMAINS", "townsquaremedia.com"),
        )

        # Azure Relay configuration (for webhooks)
        self.azure_relay = AzureRelayConfig(
            namespace=os.getenv("AZURE_RELAY_NAMESPACE", ""),
            hybrid_connection=os.getenv("AZURE_RELAY_HYBRID_CONNECTION", "teams-webhooks"),
            key_name=os.getenv("AZURE_RELAY_KEY_NAME", "RootManageSharedAccessKey"),
            key=os.getenv("AZURE_RELAY_KEY", ""),
        )

        # JWT secret for session tokens
        self.jwt_secret_key = os.getenv("JWT_SECRET_KEY", "")
        if not self.jwt_secret_key:
            import secrets
            import logging

            env_mode = os.getenv("ENV", "development").lower()
            if env_mode == "production":
                raise ValueError(
                    "CRITICAL: JWT_SECRET_KEY must be set in production! "
                    "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
            else:
                self.jwt_secret_key = secrets.token_urlsafe(32)
                logging.warning(
                    "⚠️ JWT_SECRET_KEY not set - using temporary key. "
                    "Sessions will be invalidated on restart. Set ENV=production to enforce."
                )
                print("WARNING: JWT_SECRET_KEY not set in .env, using temporary key (dev mode)")

        # Role-based access control
        self.admin_users = [u.strip().lower() for u in os.getenv("ADMIN_USERS", "").split(",") if u.strip()]
        self.manager_users = [u.strip().lower() for u in os.getenv("MANAGER_USERS", "").split(",") if u.strip()]

    def _load_yaml_config(self):
        """Load runtime configuration from config.yaml."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    data = yaml.safe_load(f) or {}

                    # Extract claude_model before passing to AppConfig (not an AppConfig field)
                    claude_model = data.pop("claude_model", None)

                    self.app = AppConfig(**data)

                    # Override Claude model if specified in config.yaml
                    if claude_model:
                        self.claude.model = claude_model
            except Exception as e:
                print(f"WARNING: Failed to load config.yaml: {e}")
                print("Using default configuration")
                self.app = AppConfig()
        else:
            # Use defaults if config file doesn't exist
            self.app = AppConfig()

    def save_yaml_config(self):
        """Save runtime configuration to config.yaml."""
        try:
            # Convert AppConfig to dict
            config_dict = {
                "polling_interval_minutes": self.app.polling_interval_minutes,
                "lookback_hours": self.app.lookback_hours,
                "pilot_mode_enabled": self.app.pilot_mode_enabled,
                "max_concurrent_jobs": self.app.max_concurrent_jobs,
                "job_timeout_minutes": self.app.job_timeout_minutes,
                "summary_max_tokens": self.app.summary_max_tokens,
                "email_enabled": self.app.email_enabled,
                "teams_chat_enabled": self.app.teams_chat_enabled,
                "minimum_meeting_duration_minutes": self.app.minimum_meeting_duration_minutes,
                "worker_heartbeat_interval_seconds": self.app.worker_heartbeat_interval_seconds,
                "use_single_call_summarization": self.app.use_single_call_summarization,
            }

            with open(self.config_file, "w") as f:
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

            print(f"Configuration saved to {self.config_file}")
        except Exception as e:
            print(f"ERROR: Failed to save config.yaml: {e}")
            raise

    def reload_yaml_config(self):
        """Reload runtime configuration from config.yaml."""
        self._load_yaml_config()

    def validate(self) -> List[str]:
        """
        Validate configuration.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate Graph API
        if not self.graph_api.client_id:
            errors.append("GRAPH_CLIENT_ID not set in .env")
        if not self.graph_api.client_secret:
            errors.append("GRAPH_CLIENT_SECRET not set in .env")
        if not self.graph_api.tenant_id:
            errors.append("GRAPH_TENANT_ID not set in .env")

        # Validate Database
        if not self.database.password:
            errors.append("DB_PASSWORD not set in .env")

        # Validate Claude API
        if not self.claude.api_key:
            errors.append("CLAUDE_API_KEY not set in .env")

        # Validate Azure AD (if enabled)
        if self.azure_ad.enabled:
            if not self.azure_ad.client_id:
                errors.append("AZURE_AD_CLIENT_ID not set in .env (SSO enabled)")
            if not self.azure_ad.client_secret:
                errors.append("AZURE_AD_CLIENT_SECRET not set in .env (SSO enabled)")
            if not self.azure_ad.tenant_id:
                errors.append("AZURE_AD_TENANT_ID not set in .env (SSO enabled)")

        # Validate JWT secret
        if not self.jwt_secret_key:
            errors.append("JWT_SECRET_KEY not set in .env")

        # Validate runtime config
        if self.app.polling_interval_minutes < 1:
            errors.append("polling_interval_minutes must be >= 1")
        if self.app.max_concurrent_jobs < 1:
            errors.append("max_concurrent_jobs must be >= 1")

        return errors


# Global singleton instance
_config: Optional[ConfigManager] = None


def get_config(env_file: Optional[str] = None, config_file: Optional[str] = None) -> ConfigManager:
    """
    Get global configuration manager instance (singleton).

    Args:
        env_file: Path to .env file (only used on first call)
        config_file: Path to config.yaml file (only used on first call)

    Returns:
        ConfigManager instance
    """
    global _config
    if _config is None:
        _config = ConfigManager(env_file=env_file, config_file=config_file)
    return _config


def reload_config():
    """Reload configuration from files."""
    global _config
    if _config is not None:
        _config.reload_yaml_config()
