"""Spectre API client — async interface to the inventory operations dashboard."""

import logging
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("conduit.spectre")

TIMEOUT = 5.0


def _base_url() -> str:
    return getattr(config, "SPECTRE_API", "http://localhost:8000").rstrip("/")


async def health_check() -> bool:
    """Ping Spectre. Returns True if reachable."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/health")
            return resp.status_code == 200
    except Exception:
        return False


async def get_inventory_summary() -> dict | None:
    """GET /api/inventory/summary — site count, total value, flagged items."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/inventory/summary")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        log.debug("Spectre inventory summary unavailable: %s", e)
    return None


async def get_site_score(site_id: str) -> dict | None:
    """GET /api/scores/{site_id} — health score, status, delta."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/scores/{site_id}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        log.debug("Spectre site score unavailable for %s: %s", site_id, e)
    return None


async def upload_file(filepath: Path, site_id: str | None = None) -> tuple[bool, bool, dict | None]:
    """POST /api/files/upload — upload inventory file.

    Returns (success, is_duplicate, response_json).
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(filepath, "rb") as f:
                files = {"file": (filepath.name, f)}
                data = {}
                if site_id:
                    data["site_id"] = site_id
                resp = await client.post(f"{_base_url()}/api/files/upload", files=files, data=data)

            if resp.status_code == 200:
                return True, False, resp.json()
            if resp.status_code == 400:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if "duplicate" in str(body.get("detail", "")).lower():
                    return True, True, body
            log.warning("Spectre upload failed (%d): %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.error("Spectre upload error: %s", e)
    return False, False, None
