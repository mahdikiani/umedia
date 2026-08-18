from server.server import create_application


def test_health_and_readiness_routes_are_registered() -> None:
    app = create_application(start_scheduler=False)
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/v1/health" in paths
    assert "/api/v1/ready" in paths
    assert 404 in app.exception_handlers
