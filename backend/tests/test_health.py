from app.main import api_health, health


def test_api_health_route() -> None:
    assert api_health() == {"status": "ok", "service": "campuscloud-api"}


def test_liveness_does_not_require_database() -> None:
    assert health() == {"status": "ok"}
