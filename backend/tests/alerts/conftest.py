import pytest


@pytest.fixture
def aclient(client):
    """`client` + the M4 routers (a no-op once app/main.py includes them)."""
    from app.main import app
    from tests.valuation.api_fixture import ensure_routers

    ensure_routers(app)
    return client
