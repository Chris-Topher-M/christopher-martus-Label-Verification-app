from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health_returns_healthy_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "ttb-label-verification",
    }


def test_root_serves_frontend() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "TTB Label Verification" in response.text


def test_root_serves_single_label_verification_form() -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="verify-form"' in html
    assert 'name="image"' in html
    assert 'name="brand_name"' in html
    assert 'name="class_type"' in html
    assert 'name="producer_name"' in html
    assert 'name="country_of_origin"' in html
    assert 'name="alcohol_by_volume"' in html
    assert 'name="net_contents"' in html
    assert 'name="government_warning"' in html
    assert "Verify Label" in html


def test_frontend_script_posts_to_verify_and_uses_readable_result_labels() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    script = response.text
    assert 'fetch("/verify"' in script
    assert "APPROVED" in script
    assert "NEEDS REVIEW" in script
    assert "Exact match required, including capital letters and punctuation." in script
    assert "Could not read this on the label." in script
