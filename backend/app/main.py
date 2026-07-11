import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend.app.verification.comparison import verify_label
from backend.app.verification.models import (
    ApplicationData,
    BatchVerificationItem,
    BatchVerificationResponse,
    BatchVerificationSummary,
    ErrorResponse,
    VerificationResult,
    VerificationVerdict,
)
from backend.app.verification.vision import (
    ImagePreprocessingError,
    OpenAIVisionService,
    VisionConfigurationError,
    VisionService,
)


BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
LATENCY_BUDGET_MS = 5000
MAX_BATCH_LABELS = 10
DEFAULT_BATCH_CONCURRENCY = 3
APPLICATION_FIELD_NAMES = (
    "brand_name",
    "class_type",
    "producer",
    "country_of_origin",
    "abv",
    "net_contents",
    "government_warning",
)
FIELD_LABELS = {
    "image": "Label Image",
    "images": "Label Images",
    "items": "Batch Item Data",
    "brand_name": "Brand Name",
    "class_type": "Class / Type",
    "producer": "Producer Name",
    "country_of_origin": "Country of Origin",
    "abv": "Alcohol by Volume",
    "net_contents": "Net Contents",
    "government_warning": "Government Warning",
}

logger = logging.getLogger(__name__)

app = FastAPI(title="TTB Label Verification")


@dataclass(frozen=True)
class BatchWorkItem:
    client_id: str
    filename: str
    image_bytes: bytes
    content_type: str | None
    application: ApplicationData | None
    error: str | None = None


def get_vision_service() -> Callable[[], VisionService]:
    return OpenAIVisionService.from_env


def _error_response(message: str, details: list[str] | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "details": details or []}}


def _raise_readable_error(status_code: int, message: str, details: list[str] | None = None) -> None:
    raise HTTPException(status_code=status_code, detail=_error_response(message, details))


@app.middleware("http")
async def log_verify_latency(request: Request, call_next: Any) -> Any:
    started_at = time.perf_counter()
    response = await call_next(request)
    if request.url.path in {"/verify", "/verify/batch"}:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if latency_ms > LATENCY_BUDGET_MS:
            logger.warning(
                "POST %s exceeded latency budget: latency_ms=%s status_code=%s",
                request.url.path,
                latency_ms,
                response.status_code,
            )
        else:
            logger.info(
                "POST %s completed: latency_ms=%s status_code=%s",
                request.url.path,
                latency_ms,
                response.status_code,
            )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
    else:
        content = _error_response(str(exc.detail))
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = [_validation_error_message(error) for error in exc.errors()]
    return JSONResponse(
        status_code=422,
        content=_error_response("Please provide all required verification fields.", details),
    )


def _validation_error_message(error: dict[str, Any]) -> str:
    location = [str(part) for part in error.get("loc", []) if part not in {"body", "form"}]
    field = ".".join(location) if location else "request"
    message = _readable_validation_message(str(error.get("msg", "Invalid value.")))
    return f"{_field_label(field)}: {message}"


def _readable_validation_message(message: str) -> str:
    replacements = {
        "Field required": "Field is required.",
        "Input should be a valid string": "Please enter text.",
    }
    cleaned = message.rstrip(".")
    return replacements.get(cleaned, f"{cleaned}.")


def _field_label(field: str) -> str:
    last_part = field.rsplit(".", maxsplit=1)[-1]
    return FIELD_LABELS.get(last_part, FIELD_LABELS.get(field, field))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "ttb-label-verification"}


@app.post(
    "/verify",
    response_model=VerificationResult,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def verify(
    image: Annotated[UploadFile, File()],
    brand_name: Annotated[str, Form()],
    class_type: Annotated[str, Form()],
    producer: Annotated[str, Form()],
    country_of_origin: Annotated[str, Form()],
    abv: Annotated[str, Form()],
    net_contents: Annotated[str, Form()],
    government_warning: Annotated[str, Form()],
    vision_service_factory: Annotated[Callable[[], VisionService], Depends(get_vision_service)],
) -> VerificationResult:
    started_at = time.perf_counter()

    application = _build_application_data(
        {
            "brand_name": brand_name,
            "class_type": class_type,
            "producer": producer,
            "country_of_origin": country_of_origin,
            "abv": abv,
            "net_contents": net_contents,
            "government_warning": government_warning,
        }
    )
    content_type = image.content_type
    if content_type not in ALLOWED_IMAGE_TYPES:
        allowed = "JPG, PNG, or WebP"
        _raise_readable_error(415, f"Please upload a {allowed} image.")

    image_bytes = await image.read()
    if not image_bytes:
        _raise_readable_error(400, "Please upload a non-empty image file.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        _raise_readable_error(413, "Please upload an image smaller than 10 MB.")

    try:
        extracted = await asyncio.to_thread(
            _extract_batch_label,
            vision_service_factory,
            image_bytes,
            content_type,
        )
        result = verify_label(application, extracted)
    except ImagePreprocessingError:
        _raise_readable_error(400, "The uploaded file is not a readable image.")
    except VisionConfigurationError:
        logger.exception("Vision service is not configured.")
        _raise_readable_error(500, "Verification is temporarily unavailable.")
    except Exception:
        logger.exception("Unexpected /verify failure.")
        _raise_readable_error(500, "Verification is temporarily unavailable.")

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return VerificationResult(
        results=result.results,
        overall_verdict=result.overall_verdict,
        latency_ms=latency_ms,
    )


@app.post(
    "/verify/batch",
    response_model=BatchVerificationResponse,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def verify_batch(
    images: Annotated[list[UploadFile], File()],
    items: Annotated[str, Form()],
    vision_service_factory: Annotated[Callable[[], VisionService], Depends(get_vision_service)],
) -> BatchVerificationResponse:
    started_at = time.perf_counter()

    raw_items = _parse_batch_items(items)
    _validate_batch_shape(images, raw_items)

    work_items = await _build_batch_work_items(images, raw_items)
    concurrency = _batch_concurrency()
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[
            _process_batch_item(work_item, semaphore, vision_service_factory)
            for work_item in work_items
        ],
        return_exceptions=True,
    )

    response_items: list[BatchVerificationItem] = []
    for work_item, result in zip(work_items, results, strict=True):
        if isinstance(result, BatchVerificationItem):
            response_items.append(result)
        else:
            logger.error(
                "Unexpected /verify/batch item failure.",
                exc_info=(type(result), result, result.__traceback__),
            )
            response_items.append(
                _batch_error_item(
                    work_item,
                    "Verification is temporarily unavailable for this label.",
                )
            )

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    passed = sum(1 for item in response_items if item.overall_verdict is VerificationVerdict.APPROVED)
    total = len(response_items)
    return BatchVerificationResponse(
        summary=BatchVerificationSummary(
            passed=passed,
            needs_review=total - passed,
            total=total,
        ),
        items=response_items,
    )


def _parse_batch_items(items_json: str) -> list[Any]:
    try:
        raw_items = json.loads(items_json)
    except json.JSONDecodeError:
        _raise_readable_error(422, "Batch item data must be valid JSON.")

    if not isinstance(raw_items, list):
        _raise_readable_error(422, "Batch item data must be a JSON array.")

    return raw_items


def _validate_batch_shape(images: list[UploadFile], raw_items: list[Any]) -> None:
    if not raw_items:
        _raise_readable_error(422, "Please add at least one label to the batch.")
    if len(raw_items) > MAX_BATCH_LABELS:
        _raise_readable_error(413, f"Please upload no more than {MAX_BATCH_LABELS} labels at a time.")
    if len(images) != len(raw_items):
        _raise_readable_error(422, "Each label image must have matching application data.")


async def _build_batch_work_items(
    images: list[UploadFile],
    raw_items: list[Any],
) -> list[BatchWorkItem]:
    work_items: list[BatchWorkItem] = []
    for index, (image, raw_item) in enumerate(zip(images, raw_items, strict=True)):
        filename = image.filename or f"label-{index + 1}"
        client_id, application, application_error = _batch_application_data(raw_item, index)
        content_type = image.content_type
        image_bytes = await image.read()

        image_error = _batch_image_error(image_bytes, content_type)
        work_items.append(
            BatchWorkItem(
                client_id=client_id,
                filename=filename,
                image_bytes=image_bytes,
                content_type=content_type,
                application=application,
                error=application_error or image_error,
            )
        )
    return work_items


def _batch_application_data(raw_item: Any, index: int) -> tuple[str, ApplicationData | None, str | None]:
    fallback_client_id = f"label-{index + 1}"
    if not isinstance(raw_item, dict):
        return fallback_client_id, None, "Application data for this label is not valid."

    raw_client_id = raw_item.get("client_id")
    client_id = str(raw_client_id).strip() if raw_client_id is not None else fallback_client_id
    if not client_id:
        client_id = fallback_client_id

    blank_fields = [
        field
        for field in APPLICATION_FIELD_NAMES
        if raw_item.get(field) is None
        or (isinstance(raw_item.get(field), str) and not raw_item.get(field, "").strip())
    ]
    if blank_fields:
        fields = ", ".join(blank_fields)
        labels = ", ".join(_field_label(field) for field in blank_fields)
        return client_id, None, f"Please complete all required application fields: {labels}."

    values = {
        field: raw_item[field].strip() if isinstance(raw_item[field], str) else raw_item[field]
        for field in APPLICATION_FIELD_NAMES
    }
    try:
        return client_id, ApplicationData.model_validate(values), None
    except ValidationError:
        return client_id, None, "Application data for this label is not valid."


def _batch_image_error(image_bytes: bytes, content_type: str | None) -> str | None:
    if content_type not in ALLOWED_IMAGE_TYPES:
        return "Please upload a JPG, PNG, or WebP image for this label."
    if not image_bytes:
        return "Please upload a non-empty image file for this label."
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return "Please upload an image smaller than 10 MB for this label."
    return None


def _batch_concurrency() -> int:
    raw_value = os.environ.get("BATCH_CONCURRENCY")
    if raw_value is None:
        return DEFAULT_BATCH_CONCURRENCY

    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning("Invalid BATCH_CONCURRENCY=%r; using default.", raw_value)
        return DEFAULT_BATCH_CONCURRENCY

    return min(max(parsed, 1), MAX_BATCH_LABELS)


async def _process_batch_item(
    work_item: BatchWorkItem,
    semaphore: asyncio.Semaphore,
    vision_service_factory: Callable[[], VisionService],
) -> BatchVerificationItem:
    started_at = time.perf_counter()

    if work_item.error is not None or work_item.application is None:
        return _batch_error_item(work_item, work_item.error or "Application data for this label is not valid.")

    try:
        async with semaphore:
            extracted = await asyncio.to_thread(
                _extract_batch_label,
                vision_service_factory,
                work_item.image_bytes,
                work_item.content_type,
            )
        result = verify_label(work_item.application, extracted)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return BatchVerificationItem(
            client_id=work_item.client_id,
            filename=work_item.filename,
            overall_verdict=result.overall_verdict,
            results=result.results,
            latency_ms=latency_ms,
            error=None,
        )
    except ImagePreprocessingError:
        return _batch_error_item(work_item, "The uploaded file is not a readable image.")
    except VisionConfigurationError:
        logger.exception("Vision service is not configured for /verify/batch.")
        return _batch_error_item(work_item, "Verification is temporarily unavailable for this label.")
    except Exception:
        logger.exception("Unexpected /verify/batch item failure.")
        return _batch_error_item(work_item, "Verification is temporarily unavailable for this label.")


def _extract_batch_label(
    vision_service_factory: Callable[[], VisionService],
    image_bytes: bytes,
    content_type: str | None,
) -> Any:
    vision_service = vision_service_factory()
    return vision_service.extract_label(image_bytes, content_type)


def _batch_error_item(work_item: BatchWorkItem, message: str) -> BatchVerificationItem:
    return BatchVerificationItem(
        client_id=work_item.client_id,
        filename=work_item.filename,
        overall_verdict=VerificationVerdict.NEEDS_REVIEW,
        results=[],
        latency_ms=0,
        error=message,
    )


def _build_application_data(fields: dict[str, str]) -> ApplicationData:
    blank_fields = [name for name, value in fields.items() if not value.strip()]
    if blank_fields:
        _raise_readable_error(
            422,
            "Please complete all required verification fields.",
            [f"{_field_label(field)}: Field is required." for field in blank_fields],
        )

    return ApplicationData(**{name: value.strip() for name, value in fields.items()})


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
