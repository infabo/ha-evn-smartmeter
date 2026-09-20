"""Constants for the EVN Smart Meter integration."""

DOMAIN = "evn_smartmeter"

CONF_FETCH_HOUR_START = "fetch_hour_start"
CONF_FETCH_HOUR_END = "fetch_hour_end"
DEFAULT_FETCH_HOUR_START = 5
DEFAULT_FETCH_HOUR_END = 7

# Per-entry external statistic id, stored in entry.data.
CONF_STATISTIC_ID = "statistic_id"
# Id used by installations before per-entry ids existed. The first
# configured account keeps it so existing Energy Dashboard setups stay valid.
LEGACY_STATISTIC_ID = f"{DOMAIN}:consumption"
