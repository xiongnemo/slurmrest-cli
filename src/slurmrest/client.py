"""Thin HTTP client for slurmrestd."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Config

_VERSION_RE = re.compile(r"^/(slurm|slurmdb)/(v[\d.]+)/")


class SlurmRestError(RuntimeError):
    """An error reported by slurmrestd, or a transport failure."""

    def __init__(self, message: str, *, status: int | None = None, errors: list | None = None):
        super().__init__(message)
        self.status = status
        self.errors = errors or []


class Client:
    def __init__(self, config: Config):
        self.config = config
        self._http = httpx.Client(
            base_url=config.url,
            headers=config.auth_headers,
            verify=config.verify_tls,
            timeout=config.timeout,
            follow_redirects=True,
        )
        self._version: str | None = config.api_version

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- version

    def discover_versions(self) -> dict[str, list[str]]:
        """Return the API versions this server advertises, per namespace."""
        spec = self._request("GET", "/openapi/v3", raw_path=True)
        found: dict[str, set[str]] = {}
        for path in (spec.get("paths") or {}):
            m = _VERSION_RE.match(path)
            if m:
                found.setdefault(m.group(1), set()).add(m.group(2))
        return {k: sorted(v) for k, v in sorted(found.items())}

    @property
    def version(self) -> str:
        """The API version in use; auto-detected on first access."""
        if self._version is None:
            versions = self.discover_versions().get("slurm") or []
            if not versions:
                raise SlurmRestError("server advertises no /slurm/vX.Y.Z endpoints")
            self._version = versions[-1]
        return self._version

    # ---------------------------------------------------------------- request

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw_path: bool = False,
        params: dict | None = None,
    ) -> dict:
        url = path if raw_path else path
        try:
            resp = self._http.request(
                method,
                url,
                json=body,
                params=params,
                headers={"Content-Type": "application/json"} if body is not None else None,
            )
        except httpx.HTTPError as exc:
            raise SlurmRestError(f"cannot reach {self.config.url}: {exc}") from exc

        text = resp.text
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            snippet = text.strip()[:200] or "(empty body)"
            raise SlurmRestError(
                f"server returned non-JSON (HTTP {resp.status_code}): {snippet}",
                status=resp.status_code,
            ) from None

        errors = payload.get("errors") or []
        if errors:
            first = errors[0]
            msg = first.get("error") or first.get("description") or "unknown error"
            raise SlurmRestError(msg, status=resp.status_code, errors=errors)
        if resp.status_code >= 400:
            raise SlurmRestError(f"HTTP {resp.status_code}", status=resp.status_code)
        return payload

    def get(self, path: str, *, namespace: str = "slurm", params: dict | None = None) -> dict:
        return self._request("GET", f"/{namespace}/{self.version}{path}", params=params)

    def post(self, path: str, body: Any, *, namespace: str = "slurm") -> dict:
        return self._request("POST", f"/{namespace}/{self.version}{path}", body=body)

    def delete(self, path: str, *, namespace: str = "slurm") -> dict:
        return self._request("DELETE", f"/{namespace}/{self.version}{path}")

    # ---------------------------------------------------------------- helpers

    def ping(self) -> dict:
        return self.get("/ping")

    def nodes(self) -> list[dict]:
        return self.get("/nodes").get("nodes") or []

    def partitions(self) -> list[dict]:
        return self.get("/partitions").get("partitions") or []

    def jobs(self) -> list[dict]:
        return self.get("/jobs").get("jobs") or []

    def job(self, job_id: str | int) -> list[dict]:
        return self.get(f"/job/{job_id}").get("jobs") or []

    def db_jobs(self, **params: Any) -> list[dict]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self.get("/jobs", namespace="slurmdb", params=clean).get("jobs") or []

    def submit(self, job: dict, script: str) -> dict:
        return self.post("/job/submit", {"job": job, "script": script})

    def cancel(self, job_id: str | int) -> dict:
        return self.delete(f"/job/{job_id}")
