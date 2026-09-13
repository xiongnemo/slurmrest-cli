"""Configuration resolution.

Order of precedence, highest first:

1. command-line flags
2. environment variables
3. the profile in ``~/.config/slurmrest/config.toml``

Nothing is hard-coded: there are no default hosts, tokens or headers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10
    import tomli as tomllib  # type: ignore[no-redef]


ENV_URL = "SLURMREST_URL"
# SLURM_JWT is what `scontrol token` prints, so accept it as a convenience.
ENV_TOKEN = ("SLURMREST_TOKEN", "SLURM_JWT")
ENV_PROFILE = "SLURMREST_PROFILE"
ENV_API_VERSION = "SLURMREST_API_VERSION"
# Extra headers as a single string: "Name: value, Other: value"
ENV_HEADERS = "SLURMREST_HEADERS"


def config_path() -> Path:
    """Location of the TOML config file (honours XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "slurmrest" / "config.toml"


def _parse_header_string(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition(":")
        if not sep:
            raise ValueError(f"malformed header {chunk!r}; expected 'Name: value'")
        headers[name.strip()] = value.strip()
    return headers


@dataclass
class Config:
    url: str
    token: str | None = None
    api_version: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    verify_tls: bool = True
    timeout: float = 60.0
    profile: str = "default"

    @property
    def auth_headers(self) -> dict[str, str]:
        out = dict(self.headers)
        if self.token:
            out["X-SLURM-USER-TOKEN"] = self.token
        return out


class ConfigError(RuntimeError):
    pass


def load(
    *,
    url: str | None = None,
    token: str | None = None,
    api_version: str | None = None,
    profile: str | None = None,
    headers: list[str] | None = None,
    insecure: bool = False,
    timeout: float | None = None,
) -> Config:
    """Build a :class:`Config` from flags, environment and config file."""
    name = profile or os.environ.get(ENV_PROFILE) or "default"

    file_conf: dict = {}
    path = config_path()
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        # Allow either a flat file or [profile-name] tables.
        file_conf = data.get(name, data if "url" in data else {})

    resolved_url = url or os.environ.get(ENV_URL) or file_conf.get("url")
    if not resolved_url:
        raise ConfigError(
            "no Slurm REST endpoint configured.\n"
            f"Set --url, ${ENV_URL}, or 'url' in {path}"
        )

    resolved_token = token
    if not resolved_token:
        for var in ENV_TOKEN:
            if os.environ.get(var):
                resolved_token = os.environ[var]
                break
    if not resolved_token:
        resolved_token = file_conf.get("token")

    merged: dict[str, str] = {}
    merged.update(file_conf.get("headers", {}) or {})
    if os.environ.get(ENV_HEADERS):
        merged.update(_parse_header_string(os.environ[ENV_HEADERS]))
    for item in headers or []:
        merged.update(_parse_header_string(item))

    return Config(
        url=str(resolved_url).rstrip("/"),
        token=resolved_token,
        api_version=api_version or os.environ.get(ENV_API_VERSION) or file_conf.get("api_version"),
        headers=merged,
        verify_tls=not (insecure or bool(file_conf.get("insecure", False))),
        timeout=timeout or float(file_conf.get("timeout", 60.0)),
        profile=name,
    )
