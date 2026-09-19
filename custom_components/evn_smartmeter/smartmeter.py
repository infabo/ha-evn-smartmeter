"""EVN/Netz NÖ Smart Meter API client.

Vendored and cleaned up from pynoesmartmeter by David Illichmann (MIT License).
https://github.com/Xlinx64/PyNoeSmartmeter

Changes from upstream:
- Removed pickle-based session persistence (not needed in HA)
- Removed aiofiles/asyncio/requests dependencies
- Replaced print() with logging
- Added proper session lifecycle management
- Use Home Assistant's prebuilt SSL context instead of letting httpx
  load the CA bundle on the event loop
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import get_default_context

from .errors import SmartmeterLoginError, SmartmeterConnectionError

_LOGGER = logging.getLogger(__name__)


class Smartmeter:
    """Async client for the Netz NÖ Smart Meter API."""

    AUTH_URL = "https://smartmeter.netz-noe.at/orchestration/Authentication/Login"
    API_BASE_URL = "https://smartmeter.netz-noe.at/orchestration"

    API_USER_DETAILS_URL = API_BASE_URL + "/User/GetBasicInfo"
    API_METERING_POINTS_URL = (
        API_BASE_URL + "/User/GetMeteringPointsByBusinesspartnerId"
    )
    API_CONSUMPTION_URL = API_BASE_URL + "/ConsumptionRecord"

    def __init__(
        self, hass: HomeAssistant, username: str, password: str
    ) -> None:
        self._hass = hass
        self._username = username
        self._password = password
        self._session: httpx.AsyncClient | None = None
        self._metering_point_id: str | None = None

    async def authenticate(self) -> bool:
        """Authenticate or validate existing session."""
        if self._session is not None:
            try:
                response = await self._session.get(self.API_USER_DETAILS_URL)
                if response.status_code == 200:
                    return True
            except httpx.RequestError:
                pass
            await self._session.aclose()
            self._session = None

        _LOGGER.debug("Starting new session and authenticating")
        # Home Assistant builds its client SSL context at import time, so
        # passing it in keeps httpx from loading the CA bundle on the event
        # loop. The client is created here rather than through
        # helpers.httpx_client.create_async_httpx_client because that helper
        # wraps aclose() in a deprecation warning and registers a shutdown
        # listener per client, neither of which suits a per-run client that
        # must keep its own login cookies.
        session = httpx.AsyncClient(
            verify=get_default_context(), timeout=30.0
        )
        auth_data = {"user": self._username, "pwd": self._password}

        _AUTH_RETRY_DELAYS = (0, 5, 15)  # seconds before each attempt
        for attempt, delay in enumerate(_AUTH_RETRY_DELAYS, start=1):
            if delay:
                _LOGGER.warning(
                    "Auth attempt %d/%d: retrying in %ds",
                    attempt, len(_AUTH_RETRY_DELAYS), delay,
                )
                await asyncio.sleep(delay)

            try:
                response = await session.post(self.AUTH_URL, data=auth_data)
            except httpx.RequestError as err:
                await session.aclose()
                raise SmartmeterConnectionError(
                    f"Connection to Smart Meter portal failed: {err}"
                ) from err

            if response.status_code == 200:
                _LOGGER.debug("Authentication successful (attempt %d)", attempt)
                self._session = session
                return True

            if response.status_code == 401:
                await session.aclose()
                raise SmartmeterLoginError("Login failed. Check username/password.")

            if response.status_code >= 500 and attempt < len(_AUTH_RETRY_DELAYS):
                _LOGGER.warning(
                    "Server error %d during authentication (attempt %d/%d)",
                    response.status_code, attempt, len(_AUTH_RETRY_DELAYS),
                )
                continue

            await session.aclose()
            raise SmartmeterConnectionError(
                f"Authentication failed with status {response.status_code}"
            )

        await session.aclose()
        raise SmartmeterConnectionError(
            f"Authentication failed after {len(_AUTH_RETRY_DELAYS)} attempts"
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.aclose()
            self._session = None

    async def _call_api(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Call the API with automatic re-authentication on 401.

        The request is sent at most twice: once with the current session
        and, if that yields 401, once more after re-authenticating.
        """
        if self._session is None:
            await self.authenticate()
        for attempt in range(2):
            try:
                response = await self._session.get(url, params=params)  # type: ignore[union-attr]
            except httpx.RequestError as err:
                raise SmartmeterConnectionError(
                    f"API request to {url} failed: {err}"
                ) from err
            if response.status_code == 200:
                return response
            if response.status_code == 401 and attempt == 0:
                _LOGGER.debug("Session expired, re-authenticating")
                await self.authenticate()
                continue
            raise SmartmeterConnectionError(
                f"API request failed with status {response.status_code}"
            )
        raise SmartmeterConnectionError("API request failed after re-authentication")

    async def get_meter_details(self) -> list[dict[str, Any]]:
        """Load all metering points for the user.

        Returns:
            List of metering point dicts. The first entry is used by default.
        """
        response = await self._call_api(
            self.API_METERING_POINTS_URL, params={"context": "2"}
        )
        meters = response.json()
        if not meters:
            raise SmartmeterConnectionError("No metering points found for this account")
        _LOGGER.debug("Metering points response: %s", meters)
        self._metering_point_id = meters[0]["meteringPointId"]
        _LOGGER.debug(
            "Found %d metering point(s), using %s",
            len(meters),
            self._metering_point_id,
        )
        return meters

    async def get_consumption_per_day(
        self, day: date
    ) -> list[float | None]:
        """Load consumption for one day (15-min intervals).

        Args:
            day: date object for the day to fetch.

        Returns:
            List of consumption values (kWh) for each 15-min interval.
            Values may be None if not yet available. An empty list means
            the portal has no data for that day.

        Raises:
            SmartmeterConnectionError: on transport errors, non-200 responses
                or an unparseable response body. Callers must not treat this
                as "no data".
            SmartmeterLoginError: if re-authentication fails.
        """
        # Portal uses non-padded format: YYYY-M-D
        day_str = f"{day.year}-{day.month}-{day.day}"
        _LOGGER.debug("Loading consumption for day %s", day_str)
        if self._metering_point_id is None:
            await self.get_meter_details()
        response = await self._call_api(
            self.API_CONSUMPTION_URL + "/Day",
            params={"meterId": self._metering_point_id, "day": day_str},
        )
        try:
            raw = response.json()
            if not raw:
                return []
            entry = raw[0] if isinstance(raw, list) else raw
            data = entry.get("ConsumptionData", entry) if isinstance(entry, dict) else entry
            # meteredValues is an indexed array of 15-min interval consumption values
            metered = data.get("meteredValues", [])
            estimated = data.get("estimatedValues", [])
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as err:
            # A 200 response that does not carry the expected JSON structure is
            # most likely a maintenance page or an API change, not "no data".
            raise SmartmeterConnectionError(
                f"Unexpected response for day {day_str}: {err}"
            ) from err
        _LOGGER.debug(
            "Day %s raw entry keys=%s meteredValues=%s estimatedValues=%s",
            day_str,
            list(data.keys()) if isinstance(data, dict) else "?",
            metered[:5] if metered else metered,
            estimated[:5] if estimated else estimated,
        )
        # Merge: use metered where available, fall back to estimated
        values = [
            m if m is not None else estimated[i] if i < len(estimated) else None
            for i, m in enumerate(metered)
        ]
        non_null = [v for v in values if v is not None]
        _LOGGER.debug(
            "Day %s: %d values, %d non-null, sum=%.3f",
            day_str, len(values), len(non_null),
            sum(non_null) if non_null else 0.0,
        )
        return values
