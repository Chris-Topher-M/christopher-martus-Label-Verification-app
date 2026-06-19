from pathlib import Path
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.verification.comparison import verify_label
from backend.app.verification.models import (
    ApplicationData,
    ErrorResponse,
    VerificationResponse,
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

logger = logging.getLogger(__name__)

app = FastAPI(title="TTB Label Verification")


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
    if request.url.path == "/verify":
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if latency_ms > LATENCY_BUDGET_MS:
            logger.warning(
                "POST /verify exceeded latency budget: latency_ms=%s status_code=%s",
                latency_ms,
                response.status_code,
            )
        else:
            logger.info(
                "POST /verify completed: latency_ms=%s status_code=%s",
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
    message = str(error.get("msg", "Invalid value."))
    return f"{field}: {message}"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "ttb-label-verification"}


@app.post(
    "/verify",
    response_model=VerificationResponse,
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
    producer_name: Annotated[str, Form()],
    country_of_origin: Annotated[str, Form()],
    alcohol_by_volume: Annotated[str, Form()],
    net_contents: Annotated[str, Form()],
    government_warning: Annotated[str, Form()],
    vision_service_factory: Annotated[Callable[[], VisionService], Depends(get_vision_service)],
) -> VerificationResponse:
    started_at = time.perf_counter()

    application = _build_application_data(
        {
            "brand_name": brand_name,
            "class_type": class_type,
            "producer_name": producer_name,
            "country_of_origin": country_of_origin,
            "alcohol_by_volume": alcohol_by_volume,
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
        vision_service = vision_service_factory()
        extracted = vision_service.extract_label(image_bytes, content_type)
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
    return VerificationResponse(verdict=result.verdict, fields=result.fields, latency_ms=latency_ms)


def _build_application_data(fields: dict[str, str]) -> ApplicationData:
    blank_fields = [name for name, value in fields.items() if not value.strip()]
    if blank_fields:
        _raise_readable_error(
            422,
            "Please complete all required verification fields.",
            [f"{field}: Field is required." for field in blank_fields],
        )

    return ApplicationData(**{name: value.strip() for name, value in fields.items()})


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
