import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset shared task state before/after each test to avoid cross-test leakage."""
    import app.state as st

    st.tasks.clear()
    st.active_downloads.clear()
    st.task_credentials.clear()
    yield
    st.tasks.clear()
    st.active_downloads.clear()
    st.task_credentials.clear()


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
