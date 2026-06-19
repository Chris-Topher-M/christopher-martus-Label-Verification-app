from enum import StrEnum

from pydantic import BaseModel, Field


class FieldStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class VerificationVerdict(StrEnum):
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ApplicationData(BaseModel):
    brand_name: str
    class_type: str
    producer_name: str
    country_of_origin: str
    alcohol_by_volume: str | float
    net_contents: str
    government_warning: str


class ExtractedLabel(BaseModel):
    brand_name: str | None = None
    class_type: str | None = None
    producer_name: str | None = None
    country_of_origin: str | None = None
    alcohol_by_volume: str | float | None = None
    net_contents: str | None = None
    government_warning: str | None = None


class FieldResult(BaseModel):
    field: str
    application_value: str | float | None
    extracted_value: str | float | None
    normalized_application_value: str | float | None
    normalized_extracted_value: str | float | None
    status: FieldStatus
    score: float | None
    message: str


class VerificationResult(BaseModel):
    verdict: VerificationVerdict
    fields: list[FieldResult]


class VerificationResponse(VerificationResult):
    latency_ms: int


class BatchVerificationSummary(BaseModel):
    passed: int
    needs_review: int
    total: int
    latency_ms: int


class BatchVerificationItem(BaseModel):
    client_id: str | None = None
    filename: str
    verdict: VerificationVerdict
    fields: list[FieldResult] = Field(default_factory=list)
    latency_ms: int
    error: str | None = None


class BatchVerificationResponse(BaseModel):
    summary: BatchVerificationSummary
    items: list[BatchVerificationItem]


class ErrorBody(BaseModel):
    message: str
    details: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
