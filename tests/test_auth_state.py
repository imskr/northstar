from __future__ import annotations


def register(client):
    return client.post(
        "/api/auth/register",
        json={"name": "Shubham", "email": "shubham@example.com", "password": "very-secure-password"},
    )


def test_registration_login_and_state_roundtrip(client):
    response = register(client)
    assert response.status_code == 201
    assert response.get_json()["user"]["name"] == "Shubham"

    state = {
        "version": 14,
        "profile": {"goal": 100000},
        "assets": {"bcfp": {"baselineShares": 1.25}},
        "transactions": [
            {
                "id": "trade-1",
                "asset": "bcfp",
                "type": "buy",
                "date": "2026-07-11",
                "shares": 1.25,
                "price": 30.35,
                "fee": 0,
                "createdAt": 123456,
            },
            {
                "id": "trade-2",
                "asset": "bcfp",
                "type": "sell",
                "date": "2026-07-12",
                "shares": 0.25,
                "price": 31.0,
                "fee": 0.2,
                "realizedPnlOverride": 0.11,
                "createdAt": 123457,
            },
        ],
    }
    saved = client.put("/api/state", json={"state": state, "revision": 0})
    assert saved.status_code == 200
    assert saved.get_json()["revision"] == 1

    loaded = client.get("/api/state")
    assert loaded.status_code == 200
    payload = loaded.get_json()
    assert payload["storage"] == "sqlite"
    assert len(payload["state"]["transactions"]) == 2
    assert payload["state"]["transactions"][1]["realizedPnlOverride"] == 0.11

    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.get("/api/state").status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"email": "shubham@example.com", "password": "very-secure-password"},
    )
    assert login.status_code == 200
    assert client.get("/api/state").get_json()["revision"] == 1


def test_rejects_invalid_trade(client):
    register(client)
    response = client.put(
        "/api/state",
        json={
            "state": {
                "transactions": [
                    {
                        "id": "bad",
                        "asset": "unknown",
                        "type": "buy",
                        "date": "2026-07-11",
                        "shares": 1,
                        "price": 10,
                    }
                ]
            }
        },
    )
    assert response.status_code == 400
