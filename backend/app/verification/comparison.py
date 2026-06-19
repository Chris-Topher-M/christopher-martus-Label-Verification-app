from difflib import SequenceMatcher
import re
import string

from backend.app.verification.models import (
    ApplicationData,
    ExtractedLabel,
    FieldResult,
    FieldStatus,
    VerificationResult,
    VerificationVerdict,
)


FUZZY_THRESHOLD = 90.0
ABV_TOLERANCE_PERCENTAGE_POINTS = 0.1
_PUNCTUATION_TRANSLATION = str.maketrans({char: " " for char in string.punctuation})

_COUNTRY_SYNONYMS = {
    "usa": "united states",
    "u s a": "united states",
    "us": "united states",
    "u s": "united states",
    "united states": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "u k": "united kingdom",
    "great britain": "united kingdom",
    "united kingdom": "united kingdom",
}


def compare_brand_name(application: str | None, extracted: str | None) -> FieldResult:
    return _compare_fuzzy("brand_name", application, extracted)


def compare_class_type(application: str | None, extracted: str | None) -> FieldResult:
    return _compare_fuzzy("class_type", application, extracted)


def compare_producer_name(application: str | None, extracted: str | None) -> FieldResult:
    return _compare_fuzzy("producer_name", application, extracted)


def compare_country_of_origin(application: str | None, extracted: str | None) -> FieldResult:
    normalized_application = _normalize_country(application)
    normalized_extracted = _normalize_country(extracted)
    passes = normalized_application is not None and normalized_application == normalized_extracted

    return _field_result(
        field="country_of_origin",
        application_value=application,
        extracted_value=extracted,
        normalized_application_value=normalized_application,
        normalized_extracted_value=normalized_extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
        score=None,
        message="Country matched after canonicalization." if passes else "Country did not match.",
    )


def compare_alcohol_by_volume(
    application: str | float | None,
    extracted: str | float | None,
) -> FieldResult:
    normalized_application = _parse_abv_percentage(application)
    normalized_extracted = _parse_abv_percentage(extracted)
    passes = (
        normalized_application is not None
        and normalized_extracted is not None
        and abs(normalized_application - normalized_extracted) <= ABV_TOLERANCE_PERCENTAGE_POINTS
    )

    return _field_result(
        field="alcohol_by_volume",
        application_value=application,
        extracted_value=extracted,
        normalized_application_value=normalized_application,
        normalized_extracted_value=normalized_extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
        score=None,
        message="ABV matched within tolerance." if passes else "ABV did not match within tolerance.",
    )


def compare_net_contents(application: str | None, extracted: str | None) -> FieldResult:
    normalized_application = _parse_net_contents_ml(application)
    normalized_extracted = _parse_net_contents_ml(extracted)
    passes = (
        normalized_application is not None
        and normalized_extracted is not None
        and abs(normalized_application - normalized_extracted) <= 0.000001
    )

    return _field_result(
        field="net_contents",
        application_value=application,
        extracted_value=extracted,
        normalized_application_value=normalized_application,
        normalized_extracted_value=normalized_extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
        score=None,
        message="Net contents matched after unit normalization." if passes else "Net contents did not match.",
    )


def compare_government_warning(application: str | None, extracted: str | None) -> FieldResult:
    passes = application is not None and extracted is not None and application == extracted

    return _field_result(
        field="government_warning",
        application_value=application,
        extracted_value=extracted,
        normalized_application_value=application,
        normalized_extracted_value=extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
        score=None,
        message=(
            "Government warning matched exactly."
            if passes
            else "Government warning must match exactly, including case and punctuation."
        ),
    )


def verify_label(application: ApplicationData, extracted: ExtractedLabel) -> VerificationResult:
    fields = [
        compare_brand_name(application.brand_name, extracted.brand_name),
        compare_class_type(application.class_type, extracted.class_type),
        compare_producer_name(application.producer_name, extracted.producer_name),
        compare_country_of_origin(application.country_of_origin, extracted.country_of_origin),
        compare_alcohol_by_volume(application.alcohol_by_volume, extracted.alcohol_by_volume),
        compare_net_contents(application.net_contents, extracted.net_contents),
        compare_government_warning(application.government_warning, extracted.government_warning),
    ]
    verdict = (
        VerificationVerdict.NEEDS_REVIEW
        if any(field.status is FieldStatus.FAIL for field in fields)
        else VerificationVerdict.PASS
    )
    return VerificationResult(verdict=verdict, fields=fields)


def _compare_fuzzy(field: str, application: str | None, extracted: str | None) -> FieldResult:
    normalized_application = _normalize_text(application)
    normalized_extracted = _normalize_text(extracted)
    score = (
        _token_sort_ratio(normalized_application, normalized_extracted)
        if normalized_application is not None and normalized_extracted is not None
        else None
    )
    passes = score is not None and score >= FUZZY_THRESHOLD

    return _field_result(
        field=field,
        application_value=application,
        extracted_value=extracted,
        normalized_application_value=normalized_application,
        normalized_extracted_value=normalized_extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
        score=score,
        message=(
            f"Fuzzy score met threshold {FUZZY_THRESHOLD:g}."
            if passes
            else f"Fuzzy score did not meet threshold {FUZZY_THRESHOLD:g}."
        ),
    )


def _field_result(
    *,
    field: str,
    application_value: str | float | None,
    extracted_value: str | float | None,
    normalized_application_value: str | float | None,
    normalized_extracted_value: str | float | None,
    status: FieldStatus,
    score: float | None,
    message: str,
) -> FieldResult:
    return FieldResult(
        field=field,
        application_value=application_value,
        extracted_value=extracted_value,
        normalized_application_value=normalized_application_value,
        normalized_extracted_value=normalized_extracted_value,
        status=status,
        score=score,
        message=message,
    )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None

    without_punctuation = value.translate(_PUNCTUATION_TRANSLATION)
    return " ".join(without_punctuation.lower().split())


def _token_sort_ratio(left: str, right: str) -> float:
    sorted_left = " ".join(sorted(left.split()))
    sorted_right = " ".join(sorted(right.split()))
    return SequenceMatcher(None, sorted_left, sorted_right).ratio() * 100


def _normalize_country(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    return _COUNTRY_SYNONYMS.get(normalized, normalized)


def _parse_abv_percentage(value: str | float | None) -> float | None:
    if value is None:
        return None

    if isinstance(value, int | float):
        return _percentage_from_number(float(value))

    percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
    if percent_match:
        return float(percent_match.group(1))

    number_match = re.search(r"\d+(?:\.\d+)?", value)
    if not number_match:
        return None

    return _percentage_from_number(float(number_match.group(0)))


def _percentage_from_number(value: float) -> float:
    if 0 <= value <= 1:
        return value * 100
    return value


def _parse_net_contents_ml(value: str | None) -> float | None:
    if value is None:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(milliliters?|ml|centiliters?|cl|liters?|l)\b",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2).lower()

    if unit in {"ml", "milliliter", "milliliters"}:
        return amount
    if unit in {"cl", "centiliter", "centiliters"}:
        return amount * 10
    if unit in {"l", "liter", "liters"}:
        return amount * 1000

    return None
