import datetime
import os

import jwt
from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def _valid_headers():
    os.environ["JWT_SECRET"] = "testsecret"
    token = jwt.encode(
        {
            "id": "testuser",
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        },
        "testsecret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_missing_required_json_field_returns_contract_error():
    response = client.post("/query", json={"query": "hello"}, headers=_valid_headers())

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert "message" in body["error"]
    assert body["error"]["details"]["field"] == "file_id"


def test_malformed_json_returns_contract_error():
    response = client.post(
        "/query",
        data="{bad-json",
        headers={**_valid_headers(), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_missing_jwt_returns_contract_error():
    os.environ["JWT_SECRET"] = "testsecret"

    response = client.post("/query", json={"query": "hello", "file_id": "file_1", "k": 1})

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert "Authorization" in body["error"]["message"]


def test_invalid_jwt_returns_contract_error():
    os.environ["JWT_SECRET"] = "testsecret"

    response = client.post(
        "/query",
        json={"query": "hello", "file_id": "file_1", "k": 1},
        headers={"Authorization": "Bearer invalid"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
