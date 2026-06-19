from __future__ import annotations

from io import BytesIO
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.verification.models import ExtractedLabel
from backend.app.verification.vision import (
    ImagePreprocessingError,
    MockVisionService,
    OpenAIVisionService,
    all_null_label,
    parse_extracted_label_response,
    preprocess_image,
)


REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN "
    "SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF "
    "THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES "
    "IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY "
    "CAUSE HEALTH PROBLEMS."
)


def _label_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer_name": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "alcohol_by_volume": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    payload.update(overrides)
    return payload


def _image_bytes(size: tuple[int, int] = (100, 80), mode: str = "RGB") -> bytes:
    image = Image.new(mode, size, (120, 30, 60))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_preprocess_downscales_large_image_and_encodes_jpeg() -> None:
    result = preprocess_image(_image_bytes(size=(3200, 1200)), "image/png")

    assert result.width == 1600
    assert result.height == 600
    assert result.content_type == "image/jpeg"
    assert result.data_url.startswith("data:image/jpeg;base64,")

    with Image.open(BytesIO(result.image_bytes)) as encoded:
        assert encoded.format == "JPEG"
        assert encoded.mode == "RGB"
        assert encoded.size == (1600, 600)


def test_preprocess_rejects_corrupt_bytes_cleanly() -> None:
    with pytest.raises(ImagePreprocessingError):
        preprocess_image(b"not an image", "image/png")


def test_openai_service_uses_injected_client_and_structured_output() -> None:
    client = _FakeClient(_label_payload())
    service = OpenAIVisionService(client=client, model="test-model", timeout_seconds=3.5)

    label = service.extract_label(_image_bytes(), "image/png")

    assert label.brand_name == "Acme Reserve"
    assert label.government_warning == REQUIRED_WARNING
    assert client.responses.calls
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["timeout"] == 3.5
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"]["additionalProperties"] is False
    image_part = call["input"][0]["content"][1]
    assert image_part["type"] == "input_image"
    assert image_part["detail"] == "high"
    assert image_part["image_url"].startswith("data:image/jpeg;base64,")


def test_unknown_placeholder_values_become_none() -> None:
    response = SimpleNamespace(output_text=json.dumps(_label_payload(brand_name="unknown")))

    label = parse_extracted_label_response(response)

    assert label.brand_name is None


def test_government_warning_is_preserved_as_single_trimmed_line() -> None:
    warning_with_line_break = REQUIRED_WARNING.replace(" WOMEN ", " WOMEN\n")
    response = SimpleNamespace(
        output_text=json.dumps(_label_payload(government_warning=f"  {warning_with_line_break}  "))
    )

    label = parse_extracted_label_response(response)

    assert label.government_warning == REQUIRED_WARNING


def test_extra_structured_field_returns_all_null_label() -> None:
    payload = _label_payload(extra_field="not allowed")

    label = parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(payload)))

    assert label == all_null_label()


def test_malformed_json_returns_all_null_label() -> None:
    label = parse_extracted_label_response(SimpleNamespace(output_text="{bad json"))

    assert label == all_null_label()


def test_wrong_field_type_returns_all_null_label() -> None:
    payload = _label_payload(brand_name=["Acme"])

    label = parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(payload)))

    assert label == all_null_label()


def test_all_null_structured_response_represents_non_label_or_unreadable_image() -> None:
    payload = {field: None for field in _label_payload()}

    label = parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(payload)))

    assert label == all_null_label()


def test_api_failure_returns_all_null_label() -> None:
    service = OpenAIVisionService(client=_FailingClient(), timeout_seconds=1)

    label = service.extract_label(_image_bytes(), "image/png")

    assert label == all_null_label()


def test_mock_vision_service_is_deterministic_and_records_calls() -> None:
    expected = ExtractedLabel(brand_name="Fixture Brand")
    service = MockVisionService(expected)
    image_bytes = _image_bytes()

    label = service.extract_label(image_bytes, "image/png")

    assert label == expected
    assert service.calls == [(image_bytes, "image/png")]


class _FakeResponses:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.payload))


class _FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.responses = _FakeResponses(payload)


class _FailingResponses:
    def create(self, **kwargs: object) -> object:
        raise TimeoutError("model timed out")


class _FailingClient:
    responses = _FailingResponses()
