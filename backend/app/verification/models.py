from enum import StrEnum

from pydantic import BaseModel, Field


class FieldStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class VerificationVerdict(StrEnum):
    APPROVED = "APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ApplicationData(BaseModel):
    brand_name: str
    class_type: str
    producer: str
    country_of_origin: str
    abv: str | float
    net_contents: str
    government_warning: str


class ExtractedLabel(BaseModel):
    brand_name: str | None = None
    class_type: str | None = None
    producer: str | None = None
    country_of_origin: str | None = None
    abv: str | float | None = None
    net_contents: str | None = None
    government_warning: str | None = None
    raw_text: str | None = None
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)


class FieldResult(BaseModel):
    field: str
    match_type: str
    expected: str | float | None
    found: str | float | None
    status: FieldStatus


class VerificationResult(BaseModel):
    results: list[FieldResult]
    overall_verdict: VerificationVerdict
    latency_ms: int


class BatchVerificationSummary(BaseModel):
    passed: int
    needs_review: int
    total: int


class BatchVerificationItem(BaseModel):
    client_id: str | None = None
    filename: str
    overall_verdict: VerificationVerdict
    results: list[FieldResult] = Field(default_factory=list)
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
