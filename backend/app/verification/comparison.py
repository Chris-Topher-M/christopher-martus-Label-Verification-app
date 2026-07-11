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


def compare_producer(application: str | None, extracted: str | None) -> FieldResult:
    return _compare_fuzzy("producer", application, extracted)


def compare_country_of_origin(application: str | None, extracted: str | None) -> FieldResult:
    normalized_application = _normalize_country(application)
    normalized_extracted = _normalize_country(extracted)
    passes = normalized_application is not None and normalized_application == normalized_extracted

    return _field_result(
        field="country_of_origin",
        match_type="normalized",
        expected=application,
        found=extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
    )


def compare_abv(
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
        field="abv",
        match_type="normalized",
        expected=application,
        found=extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
    )


def compare_net_contents(application: str | None, extracted: str | None) -> FieldResult:
    normalized_application = _parse_net_contents_ml(application)
    normalized_extracted = _parse_net_contents_ml(extracted)
    passes = (
        normalized_application is not None
        and normalized_extracted is not None
        and abs(normalized_application - normalized_extracted) <= 1.0
    )

    return _field_result(
        field="net_contents",
        match_type="normalized",
        expected=application,
        found=extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
    )


def compare_government_warning(application: str | None, extracted: str | None) -> FieldResult:
    normalized_application = _collapse_whitespace(application)
    normalized_extracted = _collapse_whitespace(extracted)
    passes = normalized_application is not None and normalized_application == normalized_extracted

    return _field_result(
        field="government_warning",
        match_type="exact",
        expected=application,
        found=extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
    )


def verify_label(application: ApplicationData, extracted: ExtractedLabel) -> VerificationResult:
    results = [
        compare_brand_name(application.brand_name, extracted.brand_name),
        compare_class_type(application.class_type, extracted.class_type),
        compare_producer(application.producer, extracted.producer),
        compare_country_of_origin(application.country_of_origin, extracted.country_of_origin),
        compare_abv(application.abv, extracted.abv),
        compare_net_contents(application.net_contents, extracted.net_contents),
        compare_government_warning(application.government_warning, extracted.government_warning),
    ]
    verdict = (
        VerificationVerdict.NEEDS_REVIEW
        if any(field.status is FieldStatus.FAIL for field in results)
        else VerificationVerdict.APPROVED
    )
    return VerificationResult(results=results, overall_verdict=verdict, latency_ms=0)


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
        match_type="fuzzy",
        expected=application,
        found=extracted,
        status=FieldStatus.PASS if passes else FieldStatus.FAIL,
    )


def _field_result(
    *,
    field: str,
    match_type: str,
    expected: str | float | None,
    found: str | float | None,
    status: FieldStatus,
) -> FieldResult:
    return FieldResult(
        field=field,
        match_type=match_type,
        expected=expected,
        found=found,
        status=status,
    )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None

    without_punctuation = value.translate(_PUNCTUATION_TRANSLATION)
    return " ".join(without_punctuation.lower().split())


def _collapse_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip()


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

    proof_match = re.search(r"(\d+(?:\.\d+)?)\s*proof\b", value, flags=re.IGNORECASE)
    if proof_match:
        return float(proof_match.group(1)) / 2

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
        r"(\d+(?:\.\d+)?)\s*(fluid\s*ounces?|fl\.?\s*oz\.?|ounces?|oz\.?|milliliters?|ml|centiliters?|cl|liters?|l)\b",
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
    if unit.replace(".", "").replace(" ", "") in {"floz", "fluidounce", "fluidounces"}:
        return amount * 29.5735
    if unit.replace(".", "").replace(" ", "") in {"oz", "ounce", "ounces"}:
        return amount * 29.5735

    return None
