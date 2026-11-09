"""Integrated diagnostics and safe application-settings backup."""

from tests.conftest import do_setup


def test_diagnostics_is_read_only_and_redacts_sensitive_state(client, admin_credentials):
    do_setup(client, admin_credentials)
    response = client.get("/api/domain/diagnostics")
    assert response.status_code == 200
    data = response.json()
    assert data["application"]["version"] == "2.8.1"
    assert "packages" in data and "tools" in data
    assert "password" not in response.text.lower()
    assert "token" not in response.text.lower()


def test_backup_round_trip_excludes_secrets(client, admin_credentials):
    setup = do_setup(client, admin_credentials)
    headers = {"X-CSRF-Token": setup["csrf_token"]}
    from app import config
    config.update_data({
        "domain": "example.test",
        "settings": {"confirmations_enabled": False},
        "mcp_token": "must-not-export",
        "notification_channels": [{"url": "secret://token"}],
    })
    exported = client.get("/api/domain/settings/backup")
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["schema"] == 1
    assert payload["data"]["domain"] == "example.test"
    assert "mcp_token" not in payload["data"]
    assert "notification_channels" not in payload["data"]

    restored = client.post("/api/domain/settings/restore", headers=headers, json={
        "schema": 1,
        "data": {"domain": "restored.test", "settings": {"confirmations_enabled": True}},
    })
    assert restored.status_code == 200
    assert config.configured_domain() == "restored.test"
    assert config.get_settings()["confirmations_enabled"] is True
    assert config.read_data()["mcp_token"] == "must-not-export"


def test_restore_rejects_unknown_fields(client, admin_credentials):
    setup = do_setup(client, admin_credentials)
    response = client.post("/api/domain/settings/restore", headers={"X-CSRF-Token": setup["csrf_token"]}, json={
        "schema": 1, "data": {"mcp_token": "nope"}
    })
    assert response.status_code == 400
