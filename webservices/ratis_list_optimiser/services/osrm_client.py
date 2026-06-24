"""HTTP client for OSRM routing service."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_TRANSPORT_MAP = {
    "driving": "car",
    "walking": "foot",
    "cycling": "bike",
}


class OsrmError(Exception):
    """Raised when OSRM returns an error or is unreachable."""


@dataclass
class TripResult:
    geometry: str  # Encoded polyline
    distance_m: float  # Total distance in meters
    duration_s: float  # Total duration in seconds
    waypoint_order: list[int]  # Optimized visit order


@dataclass
class RouteResult:
    geometry: str
    distance_m: float
    duration_s: float


class OsrmClient:
    """Synchronous HTTP client for OSRM."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def map_transport_mode(mode: str) -> str:
        """Map user transport_mode to OSRM profile."""
        return _TRANSPORT_MAP.get(mode, "car")

    def trip(
        self,
        coordinates: list[tuple[float, float]],
        profile: str = "car",
    ) -> TripResult:
        """
        Call OSRM Trip API (TSP solver).

        coordinates: list of (lng, lat) tuples -- OSRM uses lng,lat order.
        profile: "car" | "foot" | "bike"
        """
        coords_str = ";".join(f"{lng},{lat}" for lng, lat in coordinates)
        url = f"{self.base_url}/trip/v1/{profile}/{coords_str}"
        params = {
            "roundtrip": "false",
            "source": "first",
            "geometries": "polyline",
        }
        data = self._request(url, params)

        if "trips" not in data or not data["trips"]:
            raise OsrmError("OSRM returned no trips")

        trip = data["trips"][0]
        waypoints = data.get("waypoints", [])
        order = [wp["waypoint_index"] for wp in waypoints]

        return TripResult(
            geometry=trip["geometry"],
            distance_m=trip["distance"],
            duration_s=trip["duration"],
            waypoint_order=order,
        )

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        profile: str = "car",
    ) -> RouteResult:
        """Single A->B route."""
        coords_str = f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}"
        url = f"{self.base_url}/route/v1/{profile}/{coords_str}"
        params = {"geometries": "polyline"}
        data = self._request(url, params)

        if "routes" not in data or not data["routes"]:
            raise OsrmError("OSRM returned no routes")

        route = data["routes"][0]
        return RouteResult(
            geometry=route["geometry"],
            distance_m=route["distance"],
            duration_s=route["duration"],
        )

    def _request(self, url: str, params: dict) -> dict:
        """Make HTTP GET request to OSRM."""
        try:
            with httpx.Client(timeout=self.timeout) as http:
                resp = http.get(url, params=params)
                resp.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.warning("OSRM timeout: %s", url)
            raise OsrmError(f"OSRM timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("OSRM HTTP error %s: %s", exc.response.status_code, url)
            raise OsrmError(f"OSRM HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            logger.warning("OSRM connection error: %s", exc)
            raise OsrmError(f"OSRM error: {exc}") from exc

        data = resp.json()
        if data.get("code") != "Ok":
            raise OsrmError(f"OSRM error: {data.get('code')} -- {data.get('message', '')}")
        return data
