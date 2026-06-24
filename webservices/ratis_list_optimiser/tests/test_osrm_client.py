import httpx
import pytest
import respx
from services.osrm_client import OsrmClient, OsrmError


class TestOsrmClient:
    """Tests for the OSRM HTTP client."""

    @pytest.fixture
    def client(self):
        return OsrmClient(base_url="http://osrm:5000", timeout=5)

    @respx.mock
    def test_trip_returns_ordered_waypoints(self, client):
        """Trip API should return waypoint order + geometry."""
        coords = [(2.35, 48.85), (2.38, 48.86), (2.32, 48.84)]  # lng,lat
        respx.get(
            "http://osrm:5000/trip/v1/car/2.35,48.85;2.38,48.86;2.32,48.84",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": "Ok",
                    "trips": [
                        {
                            "geometry": "mocked_polyline",
                            "distance": 5432.1,
                            "duration": 890.5,
                        }
                    ],
                    "waypoints": [
                        {"waypoint_index": 0, "location": [2.35, 48.85]},
                        {"waypoint_index": 2, "location": [2.38, 48.86]},
                        {"waypoint_index": 1, "location": [2.32, 48.84]},
                    ],
                },
            )
        )
        result = client.trip(coords, profile="car")
        assert result.geometry == "mocked_polyline"
        assert result.distance_m == 5432.1
        assert result.duration_s == 890.5
        assert result.waypoint_order == [0, 2, 1]

    @respx.mock
    def test_trip_with_foot_profile(self, client):
        """Trip should use the correct profile in URL."""
        coords = [(2.35, 48.85), (2.38, 48.86)]
        respx.get(
            "http://osrm:5000/trip/v1/foot/2.35,48.85;2.38,48.86",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": "Ok",
                    "trips": [{"geometry": "poly", "distance": 100, "duration": 60}],
                    "waypoints": [
                        {"waypoint_index": 0, "location": [2.35, 48.85]},
                        {"waypoint_index": 1, "location": [2.38, 48.86]},
                    ],
                },
            )
        )
        result = client.trip(coords, profile="foot")
        assert result.geometry == "poly"

    @respx.mock
    def test_route_single_leg(self, client):
        """Route API for A->B."""
        origin = (2.35, 48.85)
        destination = (2.38, 48.86)
        respx.get(
            "http://osrm:5000/route/v1/car/2.35,48.85;2.38,48.86",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": "Ok",
                    "routes": [
                        {
                            "geometry": "route_poly",
                            "distance": 1200.0,
                            "duration": 300.0,
                        }
                    ],
                },
            )
        )
        result = client.route(origin, destination, profile="car")
        assert result.geometry == "route_poly"
        assert result.distance_m == 1200.0

    @respx.mock
    def test_trip_osrm_error(self, client):
        """OSRM returns non-Ok code -> raise OsrmError."""
        coords = [(2.35, 48.85), (2.38, 48.86)]
        respx.get(
            "http://osrm:5000/trip/v1/car/2.35,48.85;2.38,48.86",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": "NoTrips",
                    "message": "No trips found",
                },
            )
        )
        with pytest.raises(OsrmError):
            client.trip(coords, profile="car")

    @respx.mock
    def test_trip_http_error(self, client):
        """OSRM HTTP 500 -> raise OsrmError."""
        coords = [(2.35, 48.85), (2.38, 48.86)]
        respx.get(
            "http://osrm:5000/trip/v1/car/2.35,48.85;2.38,48.86",
        ).mock(return_value=httpx.Response(500))
        with pytest.raises(OsrmError):
            client.trip(coords, profile="car")

    @respx.mock
    def test_trip_timeout(self, client):
        """OSRM timeout -> raise OsrmError."""
        coords = [(2.35, 48.85), (2.38, 48.86)]
        respx.get(
            "http://osrm:5000/trip/v1/car/2.35,48.85;2.38,48.86",
        ).mock(side_effect=httpx.ReadTimeout("timeout"))
        with pytest.raises(OsrmError):
            client.trip(coords, profile="car")

    def test_transport_mode_mapping(self, client):
        """Verify transport mode -> OSRM profile mapping."""
        assert client.map_transport_mode("driving") == "car"
        assert client.map_transport_mode("walking") == "foot"
        assert client.map_transport_mode("cycling") == "bike"
        # Unknown -> default to car
        assert client.map_transport_mode("unknown") == "car"
