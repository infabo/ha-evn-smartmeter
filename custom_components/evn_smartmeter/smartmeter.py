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

import logging
from typing import Any

import httpx

from .errors import SmartmeterLoginError, SmartmeterConnectionError

_LOGGER = logging.getLogger(__name__)


class Smartmeter:
    """Async client for the Netz NÖ Smart Meter API."""

    AUTH_URL = "https://smartmeter.netz-noe.at/orchestration/Authentication/Login"
    API_BASE_URL = "https://smartmeter.netz-noe.at/orchestration"

    API_USER_DETAILS_URL = API_BASE_URL + "/User/GetBasicInfo"
    API_ACCOUNTING_DETAILS_URL = (
        API_BASE_URL + "/User/GetAccountIdByBussinespartnerId"
    )
    API_METER_DETAILS_URL = API_BASE_URL + "/User/GetMeteringPointByAccountId"
    API_CONSUMPTION_URL = API_BASE_URL + "/ConsumptionRecord"

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._session: httpx.AsyncClient | None = None
        self._metering_point_id: str | None = None
        self._account_id: str | None = None

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
        session = httpx.AsyncClient(timeout=30.0)
        auth_data = {"user": self._username, "pwd": self._password}

        try:
            response = await session.post(self.AUTH_URL, data=auth_data)
        except httpx.RequestError as err:
            await session.aclose()
            raise SmartmeterConnectionError(
                f"Connection to Smart Meter portal failed: {err}"
            ) from err

        if response.status_code == 200:
            _LOGGER.debug("Authentication successful")
            self._session = session
            return True

        await session.aclose()
        if response.status_code == 401:
            raise SmartmeterLoginError("Login failed. Check username/password.")
        raise SmartmeterConnectionError(
            f"Authentication failed with status {response.status_code}"
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
        response = await self._call_api(self.API_USER_DETAILS_URL + "?context=2")
        return response.json()[0]

    async def get_accounting_details(self) -> dict[str, Any]:
        """Load accounting details."""
        response = await self._call_api(
            self.API_ACCOUNTING_DETAILS_URL + "?context=2"
        )
        entry = response.json()[0]
        self._account_id = entry["accountId"]
        return entry

    async def get_meter_details(self) -> dict[str, Any]:
        """Load meter details."""
        if self._account_id is None:
            await self.get_accounting_details()
        response = await self._call_api(
            self.API_METER_DETAILS_URL
            + "?context=2&accountId="
            + (self._account_id or "")
        )
        entry = response.json()[0]
        self._metering_point_id = entry["meteringPointId"]
        return entry

    async def get_consumption_per_day(
        self, day: str
    ) -> list[tuple[str, float | None]]:
        """Load consumption for one day (15-min intervals).

        Args:
            day: Date string in format "YYYY-MM-DD".

        Returns:
            List of (timestamp, consumption_kwh) tuples.
        """
        _LOGGER.debug("Loading consumption for day %s", day)
        if self._metering_point_id is None:
            await self.get_meter_details()
        try:
            response = await self._call_api(
                self.API_CONSUMPTION_URL + "/Day",
                params={"meterId": self._metering_point_id, "day": day},
            )
            data = response.json()[0]
            return list(zip(data["peakDemandTimes"], data["meteredValues"]))
        except (httpx.RequestError, ValueError, KeyError, IndexError) as err:
            _LOGGER.warning("Error fetching day consumption for %s: %s", day, err)
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
