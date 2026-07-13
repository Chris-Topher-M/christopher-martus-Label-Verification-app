from __future__ import annotations

import asyncio
from collections.abc import Iterator
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app, get_vision_service
from backend.app.verification.models import ExtractedLabel
from backend.app.verification.vision import (
    ImagePreprocessingError,
    MockVisionService,
    VisionRateLimitError,
    VisionTimeoutError,
)


REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN "
    "SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF "
    "THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES "
    "IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY "
    "CAUSE HEALTH PROBLEMS."
)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_verify_happy_path_returns_full_result_and_latency(caplog: pytest.LogCaptureFixture) -> None:
    service = MockVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    with caplog.at_level("INFO"):
        response = client.post(
            "/verify",
            data=_application_form(),
            files={"image": ("label.png", _image_bytes(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["overall_verdict"] == "APPROVED"
    assert body["latency_ms"] >= 0
    assert [field["field"] for field in body["results"]] == [
        "brand_name",
        "class_type",
        "producer",
        "country_of_origin",
        "abv",
        "net_contents",
        "government_warning",
    ]
    assert all(field["status"] == "PASS" for field in body["results"])
    assert set(body) == {"results", "overall_verdict", "latency_ms"}
    assert set(body["results"][0]) == {"field", "match_type", "expected", "found", "status"}
    assert service.calls == [(_image_bytes(), "image/png")]
    assert "POST /verify completed" in caplog.text


def test_verify_mismatch_returns_expected_found_values_and_needs_review() -> None:
    extracted_warning = REQUIRED_WARNING.replace("SURGEON", "5URGEON", 1)
    service = MockVisionService(
        ExtractedLabel(
            brand_name="Mountain Cellars",
            class_type="Red Wine",
            producer="Acme Winery, LLC",
            country_of_origin="United States",
            abv="13.5%",
            net_contents="750 mL",
            government_warning=extracted_warning,
        )
    )
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overall_verdict"] == "NEEDS_REVIEW"
    failed = {field["field"]: field for field in body["results"] if field["status"] == "FAIL"}
    assert failed["brand_name"]["expected"] == "Acme Reserve"
    assert failed["brand_name"]["found"] == "Mountain Cellars"
    assert failed["government_warning"]["expected"] == REQUIRED_WARNING
    assert failed["government_warning"]["found"] == extracted_warning


def test_verify_passes_uploaded_bytes_and_content_type_to_vision_service() -> None:
    service = MockVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)
    image_bytes = _image_bytes()

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.webp", image_bytes, "image/webp")},
    )

    assert response.status_code == 200
    assert service.calls == [(image_bytes, "image/webp")]


def test_verify_runs_vision_extraction_in_the_event_loop() -> None:
    service = _EventLoopVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 200
    assert service.called_with_running_loop is True


def test_missing_image_returns_readable_422() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: MockVisionService()
    client = TestClient(app)

    response = client.post("/verify", data=_application_form())

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Please provide all required verification fields."
    assert any("Label Image" in detail for detail in response.json()["error"]["details"])


def test_missing_required_application_field_returns_readable_422() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: MockVisionService()
    client = TestClient(app)
    form = _application_form()
    form.pop("brand_name")

    response = client.post(
        "/verify",
        data=form,
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 422
    assert any("Brand Name" in detail for detail in response.json()["error"]["details"])


def test_blank_required_application_field_returns_readable_422() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: MockVisionService()
    client = TestClient(app)
    form = _application_form(brand_name="   ")

    response = client.post(
        "/verify",
        data=form,
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Please complete all required verification fields."
    assert response.json()["error"]["details"] == ["Brand Name: Field is required."]


def test_unsupported_file_type_returns_415_without_calling_vision() -> None:
    service = MockVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["message"] == "Please upload a JPG, PNG, or WebP image."
    assert service.calls == []


def test_empty_uploaded_file_returns_400_without_calling_vision() -> None:
    service = MockVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Please upload a non-empty image file."
    assert service.calls == []


def test_oversized_uploaded_file_returns_413_without_calling_vision() -> None:
    service = MockVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["message"] == "Please upload an image smaller than 10 MB."
    assert service.calls == []


def test_corrupt_image_returns_400_from_preprocessing_error() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: _PreprocessingFailureService()
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", b"not image bytes", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "The uploaded file is not a readable image."


def test_vision_service_exception_returns_safe_provider_error() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: _UnexpectedFailureService()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 502
    body_text = response.text
    assert response.json()["error"]["code"] == "VISION_UNAVAILABLE"
    assert "RuntimeError" not in body_text
    assert "traceback" not in body_text.lower()


@pytest.mark.parametrize(
    ("service", "status_code", "code"),
    [
        (VisionRateLimitError("busy"), 429, "VISION_RATE_LIMITED"),
        (VisionTimeoutError("slow"), 504, "VISION_TIMEOUT"),
    ],
)
def test_typed_vision_errors_return_safe_distinct_responses(
    service: Exception, status_code: int, code: str
) -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: _TypedFailureService(service)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_all_null_extraction_returns_needs_review_not_500() -> None:
    service = MockVisionService(ExtractedLabel())
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify",
        data=_application_form(),
        files={"image": ("label.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["overall_verdict"] == "NEEDS_REVIEW"
    assert all(field["status"] == "FAIL" for field in response.json()["results"])


def _application_form(**overrides: str) -> dict[str, str]:
    form = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    form.update(overrides)
    return form


def _image_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), (120, 30, 60))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _PreprocessingFailureService:
    async def extract_label(self, image_bytes: bytes, content_type: str | None = None) -> ExtractedLabel:
        raise ImagePreprocessingError("bad image")


class _UnexpectedFailureService:
    async def extract_label(self, image_bytes: bytes, content_type: str | None = None) -> ExtractedLabel:
        raise RuntimeError("secret failure details")


class _TypedFailureService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def extract_label(self, image_bytes: bytes, content_type: str | None = None) -> ExtractedLabel:
        raise self.error


class _EventLoopVisionService:
    def __init__(self) -> None:
        self.called_with_running_loop = False

    async def extract_label(self, image_bytes: bytes, content_type: str | None = None) -> ExtractedLabel:
        asyncio.get_running_loop()
        self.called_with_running_loop = True
        return MockVisionService().label
