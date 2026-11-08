def test_CSRFトークンの無いPOSTは400(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    response = client.post(
        "/login", data={"email": "a@example.com", "password": "password"}
    )
    assert response.status_code == 400
