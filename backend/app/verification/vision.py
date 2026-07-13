from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
import json
import logging
import os
import time
from typing import Any, Protocol

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from backend.app.verification.models import ExtractedLabel


logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "gpt-4.1-mini"
DEFAULT_TIMEOUT_SECONDS = 4.0
DEFAULT_DEADLINE_SECONDS = 4.5
DEFAULT_MAX_LONG_EDGE_PIXELS = 768
DEFAULT_JPEG_QUALITY = 80
DEFAULT_IMAGE_DETAIL = "low"
DEFAULT_MAX_OUTPUT_TOKENS = 500
MIN_LONG_EDGE_PIXELS = 512
MAX_LONG_EDGE_PIXELS = 2000
MIN_JPEG_QUALITY = 55
MAX_JPEG_QUALITY = 95
ALLOWED_IMAGE_DETAILS = {"low", "high", "auto"}

_FIELDS = (
    "brand_name",
    "class_type",
    "producer",
    "country_of_origin",
    "abv",
    "net_contents",
    "government_warning",
    "raw_text",
    "extraction_confidence",
)
_PROVIDER_TO_FIELD = {
    "brand": "brand_name",
    "type": "class_type",
    "producer": "producer",
    "country": "country_of_origin",
    "abv": "abv",
    "net": "net_contents",
    "warning": "government_warning",
    "text": "raw_text",
    "confidence": "extraction_confidence",
}
_PROVIDER_FIELDS = tuple(_PROVIDER_TO_FIELD)
_NULL_STRINGS = {
    "",
    "unknown",
    "not visible",
    "not shown",
    "n/a",
    "none",
    "null",
    "unreadable",
}


class VisionService(Protocol):
    async def extract_label(
        self, image_bytes: bytes, content_type: str | None = None
    ) -> ExtractedLabel:
        """Extract TTB label fields from one image."""
        ...


class VisionConfigurationError(RuntimeError):
    pass


class VisionServiceError(RuntimeError):
    code = "VISION_UNAVAILABLE"


class VisionAuthenticationError(VisionServiceError):
    code = "VISION_AUTHENTICATION_FAILED"


class VisionModelUnavailableError(VisionServiceError):
    code = "VISION_MODEL_UNAVAILABLE"


class VisionRateLimitError(VisionServiceError):
    code = "VISION_RATE_LIMITED"


class VisionTimeoutError(VisionServiceError):
    code = "VISION_TIMEOUT"


class VisionMalformedResponseError(VisionServiceError):
    code = "VISION_MALFORMED_RESPONSE"


class ImagePreprocessingError(ValueError):
    pass


@dataclass(frozen=True)
class PreprocessedImage:
    image_bytes: bytes
    content_type: str
    data_url: str
    width: int
    height: int


class OpenAIVisionService:
    def __init__(
        self,
        *,
        client: Any,
        model: str = DEFAULT_VISION_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        max_long_edge_pixels: int = DEFAULT_MAX_LONG_EDGE_PIXELS,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._deadline_seconds = max(deadline_seconds, 0.1)
        self._max_long_edge_pixels = _clamp_int(
            max_long_edge_pixels,
            minimum=MIN_LONG_EDGE_PIXELS,
            maximum=MAX_LONG_EDGE_PIXELS,
        )
        self._jpeg_quality = _clamp_int(
            jpeg_quality,
            minimum=MIN_JPEG_QUALITY,
            maximum=MAX_JPEG_QUALITY,
        )
        self._image_detail = (
            image_detail
            if image_detail in ALLOWED_IMAGE_DETAILS
            else DEFAULT_IMAGE_DETAIL
        )
        self._max_output_tokens = max(max_output_tokens, 200)

    @classmethod
    def from_env(cls) -> "OpenAIVisionService":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise VisionConfigurationError(
                "OPENAI_API_KEY is required for OpenAIVisionService."
            )

        from openai import AsyncOpenAI

        timeout_seconds = _env_float("VISION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        return cls(
            client=AsyncOpenAI(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            ),
            model=os.environ.get("VISION_MODEL", DEFAULT_VISION_MODEL),
            timeout_seconds=timeout_seconds,
            deadline_seconds=_env_float("VISION_DEADLINE_SECONDS", DEFAULT_DEADLINE_SECONDS),
            max_long_edge_pixels=_env_int(
                "VISION_MAX_LONG_EDGE_PIXELS", DEFAULT_MAX_LONG_EDGE_PIXELS
            ),
            jpeg_quality=_env_int("VISION_JPEG_QUALITY", DEFAULT_JPEG_QUALITY),
            image_detail=os.environ.get("VISION_IMAGE_DETAIL", DEFAULT_IMAGE_DETAIL)
            .strip()
            .lower(),
            max_output_tokens=_env_int(
                "VISION_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS
            ),
        )

    async def extract_label(
        self, image_bytes: bytes, content_type: str | None = None
    ) -> ExtractedLabel:
        started_at = time.perf_counter()
        preprocessing_ms = 0
        api_ms = 0
        parse_ms = 0
        encoded_bytes = 0
        width = 0
        height = 0
        output_tokens: int | None = None
        stage = "preprocessing"
        try:
            async with asyncio.timeout(self._deadline_seconds):
                preprocess_started_at = time.perf_counter()
                preprocessed = await asyncio.to_thread(
                    preprocess_image,
                    image_bytes,
                    content_type,
                    max_long_edge_pixels=self._max_long_edge_pixels,
                    jpeg_quality=self._jpeg_quality,
                )
                preprocessing_ms = int((time.perf_counter() - preprocess_started_at) * 1000)
                encoded_bytes = len(preprocessed.image_bytes)
                width = preprocessed.width
                height = preprocessed.height

                stage = "provider"
                api_started_at = time.perf_counter()
                response = await self._client.responses.create(
                    model=self._model,
                    instructions=_EXTRACTION_INSTRUCTIONS,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Extract the TTB label fields from this image.",
                                },
                                {
                                    "type": "input_image",
                                    "image_url": preprocessed.data_url,
                                    "detail": self._image_detail,
                                },
                            ],
                        }
                    ],
                    text={"format": _STRUCTURED_OUTPUT_FORMAT},
                    max_output_tokens=self._max_output_tokens,
                    store=False,
                    timeout=self._timeout_seconds,
                )
                api_ms = int((time.perf_counter() - api_started_at) * 1000)
                output_tokens = _response_output_tokens(response)

                stage = "parsing"
                parse_started_at = time.perf_counter()
                try:
                    label = parse_extracted_label_response(response)
                finally:
                    parse_ms = int((time.perf_counter() - parse_started_at) * 1000)
        except TimeoutError as exc:
            _log_extraction_failure(
                stage,
                preprocessing_ms,
                api_ms,
                started_at,
                self,
                source_bytes=len(image_bytes),
                encoded_bytes=encoded_bytes,
                width=width,
                height=height,
                parse_ms=parse_ms,
                output_tokens=output_tokens,
                timeout_source="absolute_deadline",
            )
            raise VisionTimeoutError("The vision service exceeded its deadline.") from exc
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Vision extraction returned invalid structured output: exception_type=%s", type(exc).__name__)
            _log_extraction_failure(
                stage,
                preprocessing_ms,
                api_ms,
                started_at,
                self,
                source_bytes=len(image_bytes),
                encoded_bytes=encoded_bytes,
                width=width,
                height=height,
                parse_ms=parse_ms,
                output_tokens=output_tokens,
                timeout_source=None,
            )
            raise VisionMalformedResponseError("The vision provider returned an unreadable result.") from exc
        except Exception as exc:
            _log_extraction_failure(
                stage,
                preprocessing_ms,
                api_ms,
                started_at,
                self,
                source_bytes=len(image_bytes),
                encoded_bytes=encoded_bytes,
                width=width,
                height=height,
                parse_ms=parse_ms,
                output_tokens=output_tokens,
                timeout_source=(
                    "provider" if type(exc).__name__ == "APITimeoutError" else None
                ),
            )
            raise _vision_service_error(exc) from exc

        total_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "Vision extraction completed: source_bytes=%s encoded_bytes=%s width=%s height=%s "
            "preprocessing_ms=%s api_ms=%s parse_ms=%s total_ms=%s output_tokens=%s "
            "detail=%s model=%s",
            len(image_bytes),
            encoded_bytes,
            preprocessed.width,
            preprocessed.height,
            preprocessing_ms,
            api_ms,
            parse_ms,
            total_ms,
            output_tokens,
            self._image_detail,
            self._model,
        )
        return label

    async def verify_configured_model(self) -> None:
        """Verify that this key can access the configured model before serving traffic."""
        try:
            await self._client.models.retrieve(self._model)
        except Exception as exc:
            raise _vision_service_error(exc) from exc


def _log_extraction_failure(
    stage: str,
    preprocessing_ms: int,
    api_ms: int,
    started_at: float,
    service: OpenAIVisionService,
    *,
    source_bytes: int,
    encoded_bytes: int,
    width: int,
    height: int,
    parse_ms: int,
    output_tokens: int | None,
    timeout_source: str | None,
) -> None:
    logger.warning(
        "Vision extraction failed: stage=%s source_bytes=%s encoded_bytes=%s width=%s height=%s "
        "preprocessing_ms=%s api_ms=%s parse_ms=%s total_ms=%s output_tokens=%s "
        "timeout_source=%s detail=%s model=%s",
        stage,
        source_bytes,
        encoded_bytes,
        width,
        height,
        preprocessing_ms,
        api_ms,
        parse_ms,
        int((time.perf_counter() - started_at) * 1000),
        output_tokens,
        timeout_source,
        service._image_detail,
        service._model,
    )


class MockVisionService:
    def __init__(self, label: ExtractedLabel | None = None) -> None:
        self.label = label or ExtractedLabel(
            brand_name="Acme Reserve",
            class_type="Red Wine",
            producer="Acme Winery, LLC",
            country_of_origin="United States",
            abv="13.5%",
            net_contents="750 mL",
            government_warning=(
                "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN "
                "SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF "
                "THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES "
                "IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY "
                "CAUSE HEALTH PROBLEMS."
            ),
        )
        self.calls: list[tuple[bytes, str | None]] = []

    async def extract_label(
        self, image_bytes: bytes, content_type: str | None = None
    ) -> ExtractedLabel:
        self.calls.append((image_bytes, content_type))
        return self.label


def all_null_label() -> ExtractedLabel:
    return ExtractedLabel()


def preprocess_image(
    image_bytes: bytes,
    content_type: str | None = None,
    *,
    max_long_edge_pixels: int = DEFAULT_MAX_LONG_EDGE_PIXELS,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> PreprocessedImage:
    try:
        with Image.open(BytesIO(image_bytes)) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImagePreprocessingError("Uploaded file is not a readable image.") from exc

    image = _flatten_to_rgb(image)
    max_long_edge_pixels = _clamp_int(
        max_long_edge_pixels,
        minimum=MIN_LONG_EDGE_PIXELS,
        maximum=MAX_LONG_EDGE_PIXELS,
    )
    jpeg_quality = _clamp_int(
        jpeg_quality, minimum=MIN_JPEG_QUALITY, maximum=MAX_JPEG_QUALITY
    )
    image.thumbnail(
        (max_long_edge_pixels, max_long_edge_pixels), Image.Resampling.LANCZOS
    )

    output = BytesIO()
    image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    encoded_bytes = output.getvalue()
    data_url = "data:image/jpeg;base64," + base64.b64encode(encoded_bytes).decode(
        "ascii"
    )

    return PreprocessedImage(
        image_bytes=encoded_bytes,
        content_type="image/jpeg",
        data_url=data_url,
        width=image.width,
        height=image.height,
    )


def parse_extracted_label_response(response: Any) -> ExtractedLabel:
    parsed = _extract_response_payload(response)
    if parsed is None:
        raise ValueError("response did not contain structured output")
    return _validate_payload(parsed)


def _vision_service_error(exc: Exception) -> VisionServiceError:
    """Map provider exceptions without returning provider text to API callers."""
    name = type(exc).__name__
    logger.warning("Vision provider request failed: exception_type=%s", name)
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return VisionAuthenticationError(
            "The vision service credentials were rejected."
        )
    if name in {"NotFoundError", "BadRequestError"}:
        return VisionModelUnavailableError(
            "The configured vision model is unavailable."
        )
    if name == "RateLimitError":
        return VisionRateLimitError("The vision service is busy.")
    if name in {"APITimeoutError", "TimeoutError"}:
        return VisionTimeoutError("The vision service timed out.")
    return VisionServiceError("The vision service is unavailable.")


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _extract_response_payload(response: Any) -> dict[str, Any] | None:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ExtractedLabel):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        loaded = json.loads(output_text)
        if not isinstance(loaded, dict):
            raise ValueError("structured output was not an object")
        return loaded

    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            payload = _payload_from_output_item(item)
            if payload is not None:
                return payload

    return None


def _payload_from_output_item(item: Any) -> dict[str, Any] | None:
    content = _get_value(item, "content")
    if not isinstance(content, list):
        return None

    for part in content:
        parsed = _get_value(part, "parsed")
        if isinstance(parsed, dict):
            return parsed

        text = _get_value(part, "text")
        if isinstance(text, str) and text.strip():
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded

    return None


def _get_value(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _response_output_tokens(response: Any) -> int | None:
    usage = _get_value(response, "usage")
    output_tokens = _get_value(usage, "output_tokens")
    if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
        return output_tokens
    return None


def _validate_payload(payload: dict[str, Any]) -> ExtractedLabel:
    if set(payload) == set(_PROVIDER_FIELDS):
        field_payload = {
            field: payload[provider_field]
            for provider_field, field in _PROVIDER_TO_FIELD.items()
        }
    elif set(payload) == set(_FIELDS):
        field_payload = payload
    else:
        raise ValueError("structured output keys did not match ExtractedLabel schema")

    cleaned = {
        field: _clean_field_value(field, field_payload[field]) for field in _FIELDS
    }
    return ExtractedLabel.model_validate(cleaned)


def _clean_field_value(field: str, value: Any) -> Any:
    if value is None or isinstance(value, int | float):
        return value
    if not isinstance(value, str):
        return value

    if value.strip().lower() in _NULL_STRINGS:
        return None
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using default.", name, raw_value)
        return default


def _env_float(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using default.", name, raw_value)
        return default


def _clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)


_EXTRACTION_INSTRUCTIONS = """
You extract text from alcohol beverage labels for TTB label verification.

Return exactly these nine compact fields and no others:
brand (brand name), type (class/type), producer, country (country of origin), abv,
net (net contents), warning (government warning), text (raw visible text), and
confidence (overall extraction confidence).

Use null when a field is absent, unreadable, uncertain, cut off, blurred, angled, obscured by glare, or only inferable from context.
Do not infer missing values from product category, common label conventions, geography, or prior knowledge.
If the image is not an alcohol or beverage product label, or the requested fields are not visible, return null for every field.
Return partial data when some fields are visible.

For warning only:
- Copy the warning exactly as visible in wording, case, spelling, punctuation, and numbering.
- Do not correct OCR mistakes, normalize case, summarize, translate, or fill missing warning text.
- If any part of the warning is unreadable or cut off, return null for warning.
- Preserve the warning's visible whitespace; do not collapse or otherwise normalize it.

For text, return a concise transcription of visible label text excluding the government warning, which is returned separately. Preserve visible case and punctuation when possible; return null if no non-warning text is readable.
For confidence, return a number from 0 to 1 representing confidence in the overall extraction; return null if it cannot be estimated.
""".strip()


_EXTRACTED_LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "brand": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "type": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "producer": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "country": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "abv": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]},
        "net": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "warning": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {
            "anyOf": [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}]
        },
    },
    "required": list(_PROVIDER_FIELDS),
    "additionalProperties": False,
}

_STRUCTURED_OUTPUT_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "extracted_label",
    "strict": True,
    "schema": _EXTRACTED_LABEL_SCHEMA,
}
