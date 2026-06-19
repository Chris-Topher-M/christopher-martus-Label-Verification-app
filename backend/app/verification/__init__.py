from backend.app.verification.comparison import verify_label
from backend.app.verification.models import (
    ApplicationData,
    ExtractedLabel,
    FieldResult,
    FieldStatus,
    VerificationResult,
    VerificationVerdict,
)

__all__ = [
    "ApplicationData",
    "ExtractedLabel",
    "FieldResult",
    "FieldStatus",
    "VerificationResult",
    "VerificationVerdict",
    "verify_label",
]
