from __future__ import annotations

from dataclasses import dataclass
import os
import re


class ConfigError(ValueError):
    """Raised when the demo's safety-oriented configuration is invalid."""


PROFILE_IDS = ("retail", "healthcare", "manufacturing")
_URI_PATTERN = re.compile(r"^mongodb(?P<srv>\+srv)?://(?:[^@/]+@)?(?P<hosts>[^/?]+)", re.IGNORECASE)


def redact(value: object) -> str:
    return re.sub(r"(mongodb(?:\+srv)?://)[^@/\s]+@", r"\1<redacted>@", str(value or ""), flags=re.IGNORECASE)


def target_label(uri: str) -> str:
    match = _URI_PATTERN.match(uri or "")
    if not match:
        return "unparseable connection string"
    scheme = "mongodb+srv" if match.group("srv") else "mongodb"
    hosts = ",".join(re.sub(r":\d+$", "", host) for host in match.group("hosts").split(","))
    return f"{scheme}://{hosts}"


def is_atlas_host(uri: str) -> bool:
    match = _URI_PATTERN.match(uri or "")
    return bool(match) and all(re.search(r"\.mongodb\.net(?::\d+)?$", host, re.IGNORECASE) for host in match.group("hosts").split(","))


def assert_demo_db_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or "demo" not in name.lower():
        raise ConfigError(f'Database name "{name}" is not allowed. Use a dedicated name that contains "demo".')
    return name


def boolean_env(env: dict[str, str], name: str, *, default: bool = False) -> bool:
    value = env.get(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no"}:
        return False
    raise ConfigError(f'{name} must be true or false. Got "{value}".')


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str | None
    db_name: str = "atlas_control_tower_demo"
    default_profile: str = "retail"
    host: str = "0.0.0.0"
    port: int = 8000
    admin_token: str | None = None
    stream_processing_enabled: bool = False

    @property
    def target(self) -> str:
        return target_label(self.mongodb_uri or "")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, require_uri: bool = True) -> "Settings":
        env = os.environ if env is None else env
        uri = env.get("MONGODB_URI")
        if not uri and require_uri:
            raise ConfigError("MONGODB_URI is not set. Copy ../.env.example to ../.env and add your Atlas connection string.")
        if uri and not is_atlas_host(uri):
            raise ConfigError(f"MONGODB_URI must point to a MongoDB Atlas cluster (*.mongodb.net). Got target {target_label(uri)}.")
        profile = env.get("DEMO_PROFILE", "retail")
        if profile not in PROFILE_IDS:
            raise ConfigError(f'DEMO_PROFILE must be one of {", ".join(PROFILE_IDS)}. Got "{profile}".')
        return cls(
            mongodb_uri=uri,
            db_name=assert_demo_db_name(env.get("DEMO_DB_NAME", "atlas_control_tower_demo")),
            default_profile=profile,
            host=env.get("HOST", "0.0.0.0"),
            # Keep the parallel FastAPI service off the Node reference app's PORT=3000.
            port=int(env.get("PYTHON_PORT", "8000")),
            admin_token=env.get("ADMIN_TOKEN") or None,
            stream_processing_enabled=boolean_env(env, "ASP_TELEMETRY_ENABLED"),
        )


def assert_safe_binding(settings: Settings) -> None:
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.admin_token:
        raise ConfigError("HOST outside loopback requires ADMIN_TOKEN because this demo exposes operational data.")