"""
Workiva API configuration constants.
"""

import os

# ── Region → Base URLs ───────────────────────────────────────────────────────
REGIONS = {
    "us": {
        "platform_base": "https://api.app.wdesk.com",
        "wdata_base": "https://h.app.wdesk.com/s/wdata/prep/api/v1",
        "chains_base": "https://h.app.wdesk.com/s/wdata/oc/api",
        "cluster_domain": "app.wdesk.com",
    },
    "eu": {
        "platform_base": "https://api.eu.wdesk.com",
        "wdata_base": "https://h.eu.wdesk.com/s/wdata/prep/api/v1",
        "chains_base": "https://h.eu.wdesk.com/s/wdata/oc/api",
        "cluster_domain": "eu.wdesk.com",
    },
    "apac": {
        "platform_base": "https://api.apac.wdesk.com",
        "wdata_base": "https://h.apac.wdesk.com/s/wdata/prep/api/v1",
        "chains_base": "https://h.apac.wdesk.com/s/wdata/oc/api",
        "cluster_domain": "apac.wdesk.com",
    },
}


def get_region():
    return os.environ.get("WORKIVA_REGION", "us").lower()


def get_urls():
    region = get_region()
    return REGIONS.get(region, REGIONS["us"])


AUTH_PATH = "/iam/v1/oauth2/token"
API_VERSION = "2026-01-01"

# ── Timing ────────────────────────────────────────────────────────────────────
TOKEN_LIFETIME = 480     # refresh at 8 min (token expires at 10)
POLL_INTERVAL = 2.0      # seconds between async operation polls
POLL_TIMEOUT = 300       # max seconds to wait for async operations
RATE_LIMIT_BACKOFF = [5, 10, 20]  # seconds for 429 retries

# ── Output limits ─────────────────────────────────────────────────────────────
MAX_CELL_ROWS = 500      # max rows returned per cell read
MAX_LIST_ITEMS = 50      # max items in list responses
