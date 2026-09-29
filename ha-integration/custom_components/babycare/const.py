"""Constants for the BabyCare Home Assistant integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "babycare"
DEFAULT_NAME = "BabyCare 育儿宝"
DEFAULT_PORT = 8090
DEFAULT_PATH = ""
DEFAULT_SCAN_INTERVAL = 60

CONF_API_KEY = "api_key"
CONF_PATH = "path"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

ATTR_AMOUNT = "amount"
ATTR_BABY = "baby"
ATTR_BABY_ID = "baby_id"
ATTR_DURATION = "duration_minutes"
ATTR_END = "end_time"
ATTR_NOTE = "note"
ATTR_START = "start_time"
ATTR_TYPE = "type"
ATTR_SIDE = "side"
ATTR_HEIGHT = "height"
ATTR_WEIGHT = "weight"
ATTR_HEAD = "head_circumference"
ATTR_RECORD_DATE = "record_date"
ATTR_DIAPER_TYPE = "diaper_type"
ATTR_SLEEP_QUALITY = "sleep_quality"
