"""Synchronous HTTP calls into services/agent-core's internal endpoints.

Not a contract interface (§3 only specifies the REST API this service exposes
and the Pub/Sub topics both services touch) -- this is a private wire between
two services SWARM owns, documented in both READMEs. See
`services/agent-core/main.py`'s module docstring for why a synchronous call
exists alongside the real Pub/Sub publish this service also does for the same
event: a live demo recording should not depend on push-delivery latency for
an upstream service (RELAY's Gmail intake) that does not exist in this repo
yet.
"""

from __future__ import annotations

import httpx

from . import config


async def process_document(case_id: str, doc_id: str) -> dict:
    async with httpx.AsyncClient(timeout=config.AGENT_CORE_TIMEOUT_S) as client:
        resp = await client.post(
            f"{config.AGENT_CORE_URL}/internal/process_document",
            json={"case_id": case_id, "doc_id": doc_id},
        )
        resp.raise_for_status()
        return resp.json()


async def approve_filing(case_id: str, front: str) -> dict:
    async with httpx.AsyncClient(timeout=config.AGENT_CORE_TIMEOUT_S) as client:
        resp = await client.post(
            f"{config.AGENT_CORE_URL}/internal/approve_filing",
            json={"case_id": case_id, "front": front},
        )
        resp.raise_for_status()
        return resp.json()
