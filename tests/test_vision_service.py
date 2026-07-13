from __future__ import annotations

import asyncio
from io import BytesIO
import json
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.verification.models import ExtractedLabel
from backend.app.verification.vision import (
    ImagePreprocessingError,
    MockVisionService,
    OpenAIVisionService,
    VisionMalformedResponseError,
    VisionTimeoutError,
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
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "raw_text": "Acme Reserve Red Wine",
        "extraction_confidence": 0.95,
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    payload.update(overrides)
    return payload


def _provider_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "brand": "Acme Reserve",
        "type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country": "United States",
        "abv": "13.5%",
        "net": "750 mL",
        "warning": REQUIRED_WARNING,
        "text": "Acme Reserve Red Wine",
        "confidence": 0.95,
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

    assert result.width == 768
    assert result.height == 288
    assert result.content_type == "image/jpeg"
    assert result.data_url.startswith("data:image/jpeg;base64,")

    with Image.open(BytesIO(result.image_bytes)) as encoded:
        assert encoded.format == "JPEG"
        assert encoded.mode == "RGB"
        assert encoded.size == (768, 288)


def test_preprocess_honors_configured_size_and_quality_bounds() -> None:
    result = preprocess_image(
        _image_bytes(size=(3200, 1200)),
        "image/png",
        max_long_edge_pixels=1024,
        jpeg_quality=70,
    )

    assert result.width == 1024
    assert result.height == 384
    assert len(result.image_bytes) < len(
        preprocess_image(_image_bytes(size=(3200, 1200)), "image/png", max_long_edge_pixels=1600).image_bytes
    )


def test_preprocess_allows_512_pixel_candidate_and_preserves_aspect_ratio() -> None:
    result = preprocess_image(
        _image_bytes(size=(3200, 1200)),
        "image/png",
        max_long_edge_pixels=512,
        jpeg_quality=80,
    )

    assert (result.width, result.height) == (512, 192)
    with Image.open(BytesIO(result.image_bytes)) as encoded:
        assert encoded.mode == "RGB"
        assert encoded.size == (512, 192)


def test_preprocess_flattens_transparency_to_white_rgb_jpeg() -> None:
    image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    output = BytesIO()
    image.save(output, format="PNG")

    result = preprocess_image(output.getvalue(), "image/png")

    with Image.open(BytesIO(result.image_bytes)) as encoded:
        assert encoded.mode == "RGB"
        assert encoded.getpixel((0, 0)) == (255, 255, 255)


def test_preprocess_rejects_corrupt_bytes_cleanly() -> None:
    with pytest.raises(ImagePreprocessingError):
        preprocess_image(b"not an image", "image/png")


def test_openai_service_uses_injected_client_and_compact_structured_output() -> None:
    client = _FakeClient(_provider_payload())
    service = OpenAIVisionService(
        client=client,
        model="test-model",
        timeout_seconds=3.5,
        max_long_edge_pixels=1024,
        jpeg_quality=75,
        image_detail="low",
        max_output_tokens=350,
    )

    label = _run_async(service.extract_label(_image_bytes(), "image/png"))

    assert label.brand_name == "Acme Reserve"
    assert label.government_warning == REQUIRED_WARNING
    assert client.responses.calls
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["timeout"] == 3.5
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"]["additionalProperties"] is False
    assert set(call["text"]["format"]["schema"]["properties"]) == {
        "brand",
        "type",
        "producer",
        "country",
        "abv",
        "net",
        "warning",
        "text",
        "confidence",
    }
    image_part = call["input"][0]["content"][1]
    assert image_part["type"] == "input_image"
    assert image_part["detail"] == "low"
    assert image_part["image_url"].startswith("data:image/jpeg;base64,")
    assert call["max_output_tokens"] == 350
    assert "excluding the government warning" in call["instructions"]


def test_openai_service_from_env_uses_tuning_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("VISION_MODEL", "test-model")
    monkeypatch.setenv("VISION_TIMEOUT_SECONDS", "3.25")
    monkeypatch.setenv("VISION_DEADLINE_SECONDS", "4.25")
    monkeypatch.setenv("VISION_MAX_LONG_EDGE_PIXELS", "1024")
    monkeypatch.setenv("VISION_JPEG_QUALITY", "70")
    monkeypatch.setenv("VISION_IMAGE_DETAIL", "low")
    monkeypatch.setenv("VISION_MAX_OUTPUT_TOKENS", "300")

    service = OpenAIVisionService.from_env()

    assert service._model == "test-model"
    assert service._timeout_seconds == 3.25
    assert service._deadline_seconds == 4.25
    assert service._client.max_retries == 0
    assert service._max_long_edge_pixels == 1024
    assert service._jpeg_quality == 70
    assert service._image_detail == "low"
    assert service._max_output_tokens == 300


def test_unknown_placeholder_values_become_none() -> None:
    response = SimpleNamespace(output_text=json.dumps(_label_payload(brand_name="unknown")))

    label = parse_extracted_label_response(response)

    assert label.brand_name is None


def test_compact_provider_payload_maps_to_public_extracted_label() -> None:
    response = SimpleNamespace(output_text=json.dumps(_provider_payload()))

    label = parse_extracted_label_response(response)

    assert label.brand_name == "Acme Reserve"
    assert label.class_type == "Red Wine"
    assert label.country_of_origin == "United States"
    assert label.net_contents == "750 mL"
    assert label.government_warning == REQUIRED_WARNING


def test_government_warning_preserves_visible_whitespace() -> None:
    warning_with_line_break = REQUIRED_WARNING.replace(" WOMEN ", " WOMEN\n")
    response = SimpleNamespace(
        output_text=json.dumps(_label_payload(government_warning=f"  {warning_with_line_break}  "))
    )

    label = parse_extracted_label_response(response)

    assert label.government_warning == f"  {warning_with_line_break}  ".strip()


def test_extraction_metadata_is_parsed() -> None:
    label = parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(_label_payload())))

    assert label.raw_text == "Acme Reserve Red Wine"
    assert label.extraction_confidence == 0.95


def test_extra_structured_field_raises_malformed_response_error() -> None:
    payload = _label_payload(extra_field="not allowed")

    with pytest.raises(ValueError):
        parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(payload)))


def test_malformed_json_raises_malformed_response_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_extracted_label_response(SimpleNamespace(output_text="{bad json"))


def test_wrong_field_type_raises_malformed_response_error() -> None:
    payload = _label_payload(brand_name=["Acme"])

    with pytest.raises(Exception):
        parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(payload)))


def test_all_null_structured_response_represents_non_label_or_unreadable_image() -> None:
    payload = {field: None for field in _label_payload()}

    label = parse_extracted_label_response(SimpleNamespace(output_text=json.dumps(payload)))

    assert label == all_null_label()


def test_api_timeout_raises_typed_error() -> None:
    service = OpenAIVisionService(client=_FailingClient(), timeout_seconds=1)

    with pytest.raises(VisionTimeoutError):
        _run_async(service.extract_label(_image_bytes(), "image/png"))


def test_mock_vision_service_is_deterministic_and_records_calls() -> None:
    expected = ExtractedLabel(brand_name="Fixture Brand")
    service = MockVisionService(expected)
    image_bytes = _image_bytes()

    label = _run_async(service.extract_label(image_bytes, "image/png"))

    assert label == expected
    assert service.calls == [(image_bytes, "image/png")]


class _FakeResponses:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(self.payload),
            usage=SimpleNamespace(output_tokens=187),
        )


class _FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.responses = _FakeResponses(payload)


class _FailingResponses:
    async def create(self, **kwargs: object) -> object:
        raise TimeoutError("model timed out")


class _FailingClient:
    responses = _FailingResponses()


def test_success_telemetry_contains_safe_sizes_timings_and_output_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = OpenAIVisionService(client=_FakeClient(_provider_payload()), model="safe-model")

    with caplog.at_level("INFO"):
        _run_async(service.extract_label(_image_bytes(), "image/png"))

    assert "source_bytes=" in caplog.text
    assert "encoded_bytes=" in caplog.text
    assert "preprocessing_ms=" in caplog.text
    assert "api_ms=" in caplog.text
    assert "parse_ms=" in caplog.text
    assert "output_tokens=187" in caplog.text
    assert "model=safe-model" in caplog.text
    assert REQUIRED_WARNING not in caplog.text


def test_failure_telemetry_does_not_log_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = OpenAIVisionService(client=_FailingClient(), timeout_seconds=1)

    with caplog.at_level("WARNING"), pytest.raises(VisionTimeoutError):
        _run_async(service.extract_label(_image_bytes(), "image/png"))

    assert "timeout_source=absolute_deadline" in caplog.text
    assert "model timed out" not in caplog.text


def test_service_deadline_cancels_a_slow_provider() -> None:
    service = OpenAIVisionService(
        client=_SlowClient(),
        timeout_seconds=5,
        deadline_seconds=0.01,
    )

    started_at = time.perf_counter()
    with pytest.raises(VisionTimeoutError):
        _run_async(service.extract_label(_image_bytes(), "image/png"))
    assert time.perf_counter() - started_at < 0.5


class _SlowResponses:
    async def create(self, **kwargs: object) -> object:
        await asyncio.sleep(1)
        return SimpleNamespace(output_text=json.dumps(_label_payload()))


class _SlowClient:
    responses = _SlowResponses()


def _run_async(coroutine: object) -> object:
    """Run a coroutine even when a browser test has an event loop in this thread."""
    result: list[object] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coroutine))  # type: ignore[arg-type]
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]
