"""Environment configuration for services/api."""

from __future__ import annotations

import os

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

# contract §3.2 topics this service publishes to.
TOPIC_CASE_DOCUMENT_ADDED = os.environ.get("TOPIC_CASE_DOCUMENT_ADDED", "case.document.added")

# services/agent-core's own URL, for the two synchronous internal calls this
# service makes (see api_core/agent_core_client.py). Not a contract interface
# (§3 only specifies REST-to-web and Pub/Sub) -- SWARM owns both services, so
# this is a private wire between them, documented in both services' READMEs.
AGENT_CORE_URL = os.environ.get("AGENT_CORE_URL", "http://localhost:8080")
AGENT_CORE_TIMEOUT_S = float(os.environ.get("AGENT_CORE_TIMEOUT_S", "170"))
