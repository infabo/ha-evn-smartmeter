"""EVN/Netz NÖ Smart Meter API client.

Vendored and cleaned up from pynoesmartmeter by David Illichmann (MIT License).
https://github.com/Xlinx64/PyNoeSmartmeter

Changes from upstream:
- Removed pickle-based session persistence (not needed in HA)
- Removed aiofiles/asyncio/requests dependencies
- Replaced print() with logging
- Added proper session lifecycle management
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

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

    def __init__(self, username: str, password: str) -> None:
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
            except (httpx.RequestError, TypeError):
                pass
            await self._session.aclose()
            self._session = None

        _LOGGER.debug("Starting new session and authenticating")
        # Create client in executor to avoid blocking the event loop
        # (httpx loads SSL certificates in the constructor)
        session = await asyncio.to_thread(httpx.AsyncClient, timeout=30.0)
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
        """Call the API with automatic re-authentication on 401."""
        if self._session is None:
            await self.authenticate()
        retry_count = 0
        while retry_count < 1:
            response = await self._session.get(url, params=params)  # type: ignore[union-attr]
            if response.status_code == 401:
                await self.authenticate()
                retry_count += 1
            elif response.status_code == 200:
                return response
            else:
                raise SmartmeterConnectionError(
                    f"API request failed with status {response.status_code}"
                )
        raise SmartmeterConnectionError("API request failed after re-authentication")

    async def get_user_details(self) -> dict[str, Any]:
        """Load user details."""
        response = await self._call_api(
            self.API_USER_DETAILS_URL, params={"context": "2"}
        )
        return response.json()[0]

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
            Values may be None if not yet available.
        """
        # Portal uses non-padded format: YYYY-M-D
        day_str = f"{day.year}-{day.month}-{day.day}"
        _LOGGER.debug("Loading consumption for day %s", day_str)
        if self._metering_point_id is None:
            await self.get_meter_details()
        try:
            response = await self._call_api(
                self.API_CONSUMPTION_URL + "/Day",
                params={"meterId": self._metering_point_id, "day": day_str},
            )
            raw = response.json()
            if not raw:
                return []
            entry = raw[0] if isinstance(raw, list) else raw
            data = entry.get("ConsumptionData", entry) if isinstance(entry, dict) else entry
            # meteredValues is an indexed array of 15-min interval consumption values
            metered = data.get("meteredValues", [])
            estimated = data.get("estimatedValues", [])
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
        except (httpx.RequestError, ValueError, KeyError, IndexError, SmartmeterConnectionError) as err:
            _LOGGER.warning("Error fetching day consumption for %s: %s", day_str, err)
            return []

    async def get_consumption_for_month(
        self, year: int, month: int
    ) -> list[tuple[str, float | None]]:
        """Load consumption for one month (daily values).

        Returns:
            List of (timestamp, consumption_kwh) tuples.
        """
        _LOGGER.debug("Loading consumption for month %s/%s", month, year)
        if self._metering_point_id is None:
            await self.get_meter_details()
        try:
            response = await self._call_api(
                self.API_CONSUMPTION_URL + "/Month",
                params={
                    "meterId": self._metering_point_id,
                    "year": year,
                    "month": month,
                },
            )
            data = response.json()[0]
            return list(zip(data["peakDemandTimes"], data["meteredValues"]))
        except (httpx.RequestError, ValueError, KeyError, IndexError) as err:
            _LOGGER.warning(
                "Error fetching month consumption for %s/%s: %s", month, year, err
            )
            return []
