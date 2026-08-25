"""services/intake -- RELAY (persona 4) WO1, WO3/WO4's callback half.

    POST /pubsub/gmail        Pub/Sub push subscription target for the
                              `intake.email.received` topic (Gmail watch).
    POST /gmail/watch/renew   Cloud Scheduler hits this weekly -- Gmail
                              watches expire after 7 days (WO1).
    POST /webhooks/phaxio     Phaxio fax-status webhook -> filing.completed
    POST /webhooks/lob        Lob mail-status webhook -> filing.completed
    GET  /health              liveness only (see agent-core's note on why
                              this is /health, not /healthz)

Every route returns 200 even when it did nothing (duplicate delivery,
unmapped vendor id) -- Pub/Sub and the vendor webhooks both retry on
non-2xx, and agreement §2.3 requires handlers tolerate redelivery. Returning
an error for a case this code already understands to be a safe no-op would
just manufacture a retry storm.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from intake import gmail_client, pipeline, pubsub, vendor_callbacks
from intake.pubsub import BadPushEnvelope

logger = logging.getLogger("intake")

app = FastAPI(title="Every Front intake")


@app.get("/")
def root() -> dict:
    return {"service": "intake", "status": "ok"}


@app.get("/health")
def health() -> dict:
    """Liveness only -- deliberately does not touch Gmail/GCS/Pub/Sub, same
    reasoning as agent-core's `/health` (docs/SPIKE.md gate (c))."""
    return {"ok": True}


@app.post("/pubsub/gmail")
async def pubsub_gmail(request: Request) -> dict:
    body = await request.json()
    try:
        message_id, data = pubsub.decode_push_envelope(body)
    except BadPushEnvelope as exc:
        # A malformed envelope will never become well-formed on retry --
        # log it and ack (200) rather than let Pub/Sub retry forever.
        logger.error("bad Pub/Sub push envelope: %s", exc)
        return {"status": "ignored", "reason": str(exc)}

    try:
        return pipeline.process_gmail_push(message_id, data)
    except Exception:
        logger.exception("failed processing Gmail push %s", message_id)
        raise


@app.post("/gmail/watch/renew")
def renew_watch() -> dict:
    """Cloud Scheduler's weekly target -- HANDOFF: ATLAS needs a Scheduler
    job (`infra/setup.sh` is outside RELAY's owned paths) pointed at this
    route with a 7-day-or-shorter cadence, invoking as `ef-intake` via OIDC.
    """
    return gmail_client.start_watch()


@app.post("/webhooks/phaxio")
async def webhook_phaxio(request: Request) -> dict:
    payload = await request.json()
    result = vendor_callbacks.handle_vendor_callback("fax", payload)
    if result is not None:
        pubsub.publish("filing.completed", result)
    return {"status": "ok" if result else "ignored"}


@app.post("/webhooks/lob")
async def webhook_lob(request: Request) -> dict:
    payload = await request.json()
    result = vendor_callbacks.handle_vendor_callback("mail", payload)
    if result is not None:
        pubsub.publish("filing.completed", result)
    return {"status": "ok" if result else "ignored"}
