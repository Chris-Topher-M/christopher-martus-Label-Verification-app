from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app, get_vision_service
from backend.app.verification.models import ExtractedLabel


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


def test_batch_happy_path_returns_summary_and_items() -> None:
    first_image = _image_bytes((120, 30, 60))
    second_image = _image_bytes((30, 120, 60))
    service = _MappingVisionService({first_image: _matching_label(), second_image: _matching_label()})
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item("label-1"), _application_item("label-2")])},
        files=[
            ("images", ("front-1.png", first_image, "image/png")),
            ("images", ("front-2.png", second_image, "image/png")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == 2
    assert body["summary"]["passed"] == 2
    assert body["summary"]["needs_review"] == 0
    assert [item["client_id"] for item in body["items"]] == ["label-1", "label-2"]
    assert [item["filename"] for item in body["items"]] == ["front-1.png", "front-2.png"]
    assert all(item["overall_verdict"] == "APPROVED" for item in body["items"])
    assert all(item["error"] is None for item in body["items"])
    assert len(service.calls) == 2


def test_batch_mixed_verdicts_return_correct_summary_counts() -> None:
    first_image = _image_bytes((120, 30, 60))
    second_image = _image_bytes((30, 120, 60))
    service = _MappingVisionService(
        {
            first_image: _matching_label(),
            second_image: _matching_label(brand_name="Mountain Cellars"),
        }
    )
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item("label-1"), _application_item("label-2")])},
        files=[
            ("images", ("front-1.png", first_image, "image/png")),
            ("images", ("front-2.png", second_image, "image/png")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == 2
    assert body["summary"]["passed"] == 1
    assert body["summary"]["needs_review"] == 1
    assert [item["overall_verdict"] for item in body["items"]] == ["APPROVED", "NEEDS_REVIEW"]
    assert body["items"][1]["results"]


def test_bad_label_in_batch_becomes_item_error_without_failing_batch() -> None:
    valid_image = _image_bytes((120, 30, 60))
    service = _MappingVisionService({valid_image: _matching_label()})
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item("label-1"), _application_item("label-2")])},
        files=[
            ("images", ("front-1.png", valid_image, "image/png")),
            ("images", ("front-2.txt", b"not an image", "text/plain")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "passed": 1,
        "needs_review": 1,
        "total": 2,
    }
    assert body["items"][0]["overall_verdict"] == "APPROVED"
    assert body["items"][1]["overall_verdict"] == "NEEDS_REVIEW"
    assert body["items"][1]["results"] == []
    assert body["items"][1]["error"] == "Please upload a JPG, PNG, or WebP image for this label."
    assert service.calls == [(valid_image, "image/png")]


def test_one_vision_failure_does_not_fail_other_batch_items() -> None:
    first_image = _image_bytes((120, 30, 60))
    second_image = _image_bytes((30, 120, 60))
    service = _MappingVisionService({first_image: _matching_label()}, failing_images={second_image})
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item("label-1"), _application_item("label-2")])},
        files=[
            ("images", ("front-1.png", first_image, "image/png")),
            ("images", ("front-2.png", second_image, "image/png")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["passed"] == 1
    assert body["summary"]["needs_review"] == 1
    assert body["items"][0]["overall_verdict"] == "APPROVED"
    assert body["items"][1]["overall_verdict"] == "NEEDS_REVIEW"
    assert body["items"][1]["results"] == []
    assert body["items"][1]["error"] == "We could not read this photo. Please try again."
    assert body["items"][1]["error_code"] == "VISION_UNAVAILABLE"


def test_batch_image_and_item_count_mismatch_returns_422() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: _MappingVisionService({})
    client = TestClient(app)

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item("label-1"), _application_item("label-2")])},
        files=[("images", ("front-1.png", _image_bytes((120, 30, 60)), "image/png"))],
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Each label image must have matching application data."


def test_batch_malformed_items_json_returns_readable_422() -> None:
    app.dependency_overrides[get_vision_service] = lambda: lambda: _MappingVisionService({})
    client = TestClient(app)

    response = client.post(
        "/verify/batch",
        data={"items": "{bad json"},
        files=[("images", ("front-1.png", _image_bytes((120, 30, 60)), "image/png"))],
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Batch item data must be valid JSON."


def test_batch_blank_required_field_becomes_item_error_without_calling_vision() -> None:
    image = _image_bytes((120, 30, 60))
    service = _MappingVisionService({image: _matching_label()})
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)
    item = _application_item("label-1")
    item["government_warning"] = "   "

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([item])},
        files=[("images", ("front-1.png", image, "image/png"))],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["passed"] == 0
    assert body["summary"]["needs_review"] == 1
    assert body["items"][0]["overall_verdict"] == "NEEDS_REVIEW"
    assert body["items"][0]["error"] == "Please complete all required application fields: Government Warning."
    assert service.calls == []


def test_batch_empty_uploaded_file_becomes_item_error_without_failing_batch() -> None:
    valid_image = _image_bytes((120, 30, 60))
    service = _MappingVisionService({valid_image: _matching_label()})
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item("label-1"), _application_item("label-2")])},
        files=[
            ("images", ("front-1.png", valid_image, "image/png")),
            ("images", ("front-2.png", b"", "image/png")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["passed"] == 1
    assert body["summary"]["needs_review"] == 1
    assert body["items"][1]["error"] == "Please upload a non-empty image file for this label."
    assert service.calls == [(valid_image, "image/png")]


def test_batch_processes_labels_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATCH_CONCURRENCY", "3")
    images = [
        _image_bytes((120, 30, 60)),
        _image_bytes((30, 120, 60)),
        _image_bytes((60, 30, 120)),
    ]
    service = _MappingVisionService({image: _matching_label() for image in images}, delay_seconds=0.25)
    app.dependency_overrides[get_vision_service] = lambda: lambda: service
    client = TestClient(app)

    started_at = time.perf_counter()
    response = client.post(
        "/verify/batch",
        data={"items": json.dumps([_application_item(f"label-{index}") for index in range(3)])},
        files=[
            ("images", (f"front-{index}.png", image, "image/png"))
            for index, image in enumerate(images)
        ],
    )
    elapsed = time.perf_counter() - started_at

    assert response.status_code == 200
    assert response.json()["summary"]["passed"] == 3
    assert len(service.calls) == 3
    assert elapsed < 0.55


def _application_item(client_id: str) -> dict[str, str]:
    return {
        "client_id": client_id,
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }


def _matching_label(**overrides: object) -> ExtractedLabel:
    data = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    data.update(overrides)
    return ExtractedLabel(**data)


def _image_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (8, 8), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _MappingVisionService:
    def __init__(
        self,
        labels_by_image: dict[bytes, ExtractedLabel],
        *,
        failing_images: set[bytes] | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.labels_by_image = labels_by_image
        self.failing_images = failing_images or set()
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[bytes, str | None]] = []
        self._lock = threading.Lock()

    def extract_label(self, image_bytes: bytes, content_type: str | None = None) -> ExtractedLabel:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)

        with self._lock:
            self.calls.append((image_bytes, content_type))

        if image_bytes in self.failing_images:
            raise RuntimeError("boom")

        return self.labels_by_image[image_bytes]
