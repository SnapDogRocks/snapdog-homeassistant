"""Constants of the SnapDog integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "snapdog"
LOGGER = logging.getLogger(__package__)

CONF_API_KEY: Final = "api_key"
CONF_TLS: Final = "tls"
CONF_VERIFY_TLS: Final = "verify_tls"
CONF_SERVER_ID: Final = "server_id"

DEFAULT_PORT: Final = 5555
MINIMUM_SERVER_VERSION: Final = (0, 31, 0)

# A position that departs from the extrapolated one by more than this many seconds is
# written to the state machine. A zone sends its position about once a second, so smaller
# steps would only fill the database.
POSITION_TOLERANCE: Final = 2.0
