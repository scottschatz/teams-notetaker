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
    """Anthropic Claude API configuration."""

    api_key: str
    model: str = "claude-sonnet-4-20250514"
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
    allowed_domain: str = "townsquaremedia.com"
    authority: str = ""

    def __post_init__(self):
        """Build authority URL from tenant ID if not provided."""
        if self.tenant_id and not self.authority:
            self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

    def is_whitelist_enabled(self) -> bool:
        """Check if user whitelist is enabled."""
        # For now, just domain validation
        # Can be extended to check specific allowed users from env
        return False


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

    # Filtering
    minimum_meeting_duration_minutes: int = 5

    # Worker configuration
    worker_heartbeat_interval_seconds: int = 30


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
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
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
            allowed_domain=os.getenv("AZURE_AD_ALLOWED_DOMAIN", "townsquaremedia.com"),
        )

        # JWT secret for session tokens
        self.jwt_secret_key = os.getenv("JWT_SECRET_KEY", "")
        if not self.jwt_secret_key:
            import secrets

            self.jwt_secret_key = secrets.token_urlsafe(32)
            print("WARNING: JWT_SECRET_KEY not set in .env, using temporary key")

        # Role-based access control
        self.admin_users = [u.strip().lower() for u in os.getenv("ADMIN_USERS", "").split(",") if u.strip()]
        self.manager_users = [u.strip().lower() for u in os.getenv("MANAGER_USERS", "").split(",") if u.strip()]

    def _load_yaml_config(self):
        """Load runtime configuration from config.yaml."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    data = yaml.safe_load(f) or {}
                    self.app = AppConfig(**data)
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
