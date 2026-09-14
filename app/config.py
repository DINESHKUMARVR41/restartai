"""Runtime configuration loaded once by the FastAPI process.

Secrets remain in environment variables and are never serialized by this module.
"""
from dataclasses import dataclass
import os


def _value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    call_e_mode: str
    calle_api_key: str
    calle_base_url: str
    poll_interval_seconds: float
    poll_timeout_seconds: int

    @property
    def live(self) -> bool:
        return self.call_e_mode == "live"

    @property
    def configured(self) -> bool:
        return self.call_e_mode == "demo" or bool(self.calle_api_key)

    @property
    def configuration_error(self) -> str | None:
        if self.call_e_mode not in {"demo", "live"}:
            return "CALL_E_MODE must be either 'demo' or 'live'."
        if self.live and not self.calle_api_key:
            return "LIVE CALL-E is not configured. Add CALLE_API_KEY to .env and restart the backend."
        return None


def load_settings() -> Settings:
    mode = _value("CALL_E_MODE", "demo").lower()
    return Settings(
        call_e_mode=mode,
        calle_api_key=_value("CALLE_API_KEY"),
        calle_base_url=_value("CALLE_BASE_URL", "https://api.heycall-e.com").rstrip("/"),
        poll_interval_seconds=max(0.5, float(_value("CALLE_POLL_INTERVAL_SECONDS", "2"))),
        poll_timeout_seconds=max(1, int(float(_value("CALLE_POLL_TIMEOUT_SECONDS", "300")))),
    )
