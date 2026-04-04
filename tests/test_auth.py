"""Bearer authentication middleware tests."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_health_always_accessible(client):
    """Health endpoint must be accessible without auth."""
    r = client.get("/health")
    assert r.status_code == 200


@patch("app.config.AUTH_TOKEN", "secret123")
def test_tasks_rejected_without_token():
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/tasks")
        assert r.status_code == 401


@patch("app.config.AUTH_TOKEN", "secret123")
def test_tasks_allowed_with_bearer_header():
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/tasks", headers={"Authorization": "Bearer secret123"})
        assert r.status_code == 200


@patch("app.config.AUTH_TOKEN", "secret123")
def test_tasks_allowed_with_query_param():
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/tasks?token=secret123")
        assert r.status_code == 200


@patch("app.config.AUTH_TOKEN", "")
def test_no_auth_when_token_empty(client):
    """When AUTH_TOKEN is empty, all endpoints are accessible."""
    r = client.get("/tasks")
    assert r.status_code == 200
