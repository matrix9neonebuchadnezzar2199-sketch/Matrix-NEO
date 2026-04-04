import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state import tm


@pytest.fixture(autouse=True)
def _clean_state():
    tm.reset()
    yield
    tm.reset()


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
