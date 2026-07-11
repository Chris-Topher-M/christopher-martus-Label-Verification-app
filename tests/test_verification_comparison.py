import pytest

from backend.app.verification.comparison import (
    compare_abv,
    compare_brand_name,
    compare_class_type,
    compare_country_of_origin,
    compare_government_warning,
    compare_net_contents,
    compare_producer,
    verify_label,
)
from backend.app.verification.models import (
    ApplicationData,
    ExtractedLabel,
    FieldStatus,
    VerificationVerdict,
)


REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN "
    "SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF "
    "THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES "
    "IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY "
    "CAUSE HEALTH PROBLEMS."
)


def make_application(**overrides: object) -> ApplicationData:
    data = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    data.update(overrides)
    return ApplicationData(**data)


def make_extracted(**overrides: object) -> ExtractedLabel:
    data = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    data.update(overrides)
    return ExtractedLabel(**data)


def test_models_instantiate_cleanly() -> None:
    application = make_application()
    extracted = make_extracted()

    assert application.brand_name == "Acme Reserve"
    assert extracted.brand_name == "Acme Reserve"


def test_missing_extracted_field_fails_without_exception() -> None:
    result = compare_brand_name("Acme Reserve", None)

    assert result.status is FieldStatus.FAIL
    assert result.found is None


def test_brand_exact_match_passes() -> None:
    result = compare_brand_name("Acme Reserve", "Acme Reserve")

    assert result.status is FieldStatus.PASS


def test_case_only_brand_difference_passes() -> None:
    result = compare_brand_name("ACME RESERVE", "acme reserve")

    assert result.status is FieldStatus.PASS


def test_brand_punctuation_and_extra_whitespace_passes() -> None:
    result = compare_brand_name("Acme Reserve", "  Acme,   Reserve! ")

    assert result.status is FieldStatus.PASS


def test_brand_materially_different_fails() -> None:
    result = compare_brand_name("Acme Reserve", "Mountain Cellars")

    assert result.status is FieldStatus.FAIL


def test_class_type_reordered_words_passes() -> None:
    result = compare_class_type("Red Wine", "wine red")

    assert result.status is FieldStatus.PASS


def test_producer_minor_punctuation_variation_passes() -> None:
    result = compare_producer("Acme Winery LLC", "Acme Winery, LLC")

    assert result.status is FieldStatus.PASS


def test_fuzzy_score_below_threshold_fails() -> None:
    result = compare_class_type("Red Wine", "Distilled Gin")

    assert result.status is FieldStatus.FAIL
    assert result.match_type == "fuzzy"


@pytest.mark.parametrize(
    ("application", "extracted"),
    [
        ("USA", "United States"),
        ("U.S.A.", "United States of America"),
        ("UK", "United Kingdom"),
    ],
)
def test_country_synonyms_pass(application: str, extracted: str) -> None:
    result = compare_country_of_origin(application, extracted)

    assert result.status is FieldStatus.PASS


def test_different_countries_fail() -> None:
    result = compare_country_of_origin("United States", "France")

    assert result.status is FieldStatus.FAIL


def test_unknown_country_strings_fall_back_to_normalized_exact_match() -> None:
    result = compare_country_of_origin("Republic of Example", "republic-of example")

    assert result.status is FieldStatus.PASS


@pytest.mark.parametrize(
    ("application", "extracted"),
    [
        ("13.5%", "13.5"),
        ("13.5%", "0.135"),
        ("13.5%", "13.6%"),
        ("45%", "45% Alc./Vol. (90 Proof)"),
        ("45%", "90 Proof"),
    ],
)
def test_abv_equivalent_values_pass(application: object, extracted: object) -> None:
    result = compare_abv(application, extracted)

    assert result.status is FieldStatus.PASS


def test_abv_outside_tolerance_fails() -> None:
    result = compare_abv("13.5%", "13.8%")

    assert result.status is FieldStatus.FAIL


def test_unparseable_abv_fails_cleanly() -> None:
    result = compare_abv("13.5%", "unknown")

    assert result.status is FieldStatus.FAIL


@pytest.mark.parametrize(
    ("application", "extracted"),
    [
        ("750 ml", "750mL"),
        ("750 mL", "750ml"),
        ("750 ml", "0.75 L"),
        ("750 ml", "75 cl"),
        ("750 mL", "25.36 fl oz"),
    ],
)
def test_net_contents_equivalent_values_pass(application: object, extracted: object) -> None:
    result = compare_net_contents(application, extracted)

    assert result.status is FieldStatus.PASS


def test_net_contents_different_values_fail() -> None:
    result = compare_net_contents("750 ml", "375 ml")

    assert result.status is FieldStatus.FAIL


def test_net_contents_more_than_one_ml_apart_fails() -> None:
    result = compare_net_contents("750 mL", "25 fl oz")

    assert result.status is FieldStatus.FAIL


def test_unparseable_net_contents_fails_cleanly() -> None:
    result = compare_net_contents("750 ml", "unknown")

    assert result.status is FieldStatus.FAIL


def test_government_warning_exact_all_caps_passes() -> None:
    result = compare_government_warning(REQUIRED_WARNING, REQUIRED_WARNING)

    assert result.status is FieldStatus.PASS


def test_government_warning_title_case_fails() -> None:
    title_case_warning = REQUIRED_WARNING.title()

    result = compare_government_warning(REQUIRED_WARNING, title_case_warning)

    assert result.status is FieldStatus.FAIL


def test_government_warning_missing_colon_fails() -> None:
    missing_colon_warning = REQUIRED_WARNING.replace("GOVERNMENT WARNING:", "GOVERNMENT WARNING", 1)

    result = compare_government_warning(REQUIRED_WARNING, missing_colon_warning)

    assert result.status is FieldStatus.FAIL


def test_government_warning_lowercase_change_fails() -> None:
    changed_warning = REQUIRED_WARNING.replace("WARNING", "Warning", 1)

    result = compare_government_warning(REQUIRED_WARNING, changed_warning)

    assert result.status is FieldStatus.FAIL


def test_government_warning_extra_punctuation_fails() -> None:
    changed_warning = REQUIRED_WARNING.replace("HEALTH PROBLEMS.", "HEALTH PROBLEMS!", 1)

    result = compare_government_warning(REQUIRED_WARNING, changed_warning)

    assert result.status is FieldStatus.FAIL


def test_government_warning_whitespace_differences_pass() -> None:
    result = compare_government_warning(REQUIRED_WARNING, f" {REQUIRED_WARNING} ")

    assert result.status is FieldStatus.PASS


def test_missing_extracted_warning_fails() -> None:
    result = compare_government_warning(REQUIRED_WARNING, None)

    assert result.status is FieldStatus.FAIL


def test_government_warning_failure_returns_extracted_text() -> None:
    misread_warning = REQUIRED_WARNING.replace("SURGEON", "5URGEON", 1)

    result = compare_government_warning(REQUIRED_WARNING, misread_warning)

    assert result.status is FieldStatus.FAIL
    assert result.found == misread_warning


def test_all_fields_pass_returns_approved_verdict() -> None:
    result = verify_label(make_application(), make_extracted())

    assert result.overall_verdict is VerificationVerdict.APPROVED
    assert all(field.status is FieldStatus.PASS for field in result.results)


def test_one_field_failure_returns_needs_review_verdict() -> None:
    result = verify_label(make_application(), make_extracted(brand_name="Mountain Cellars"))

    assert result.overall_verdict is VerificationVerdict.NEEDS_REVIEW


def test_multiple_failures_return_all_field_results_without_fail_fast() -> None:
    result = verify_label(
        make_application(),
        make_extracted(
            brand_name="Mountain Cellars",
            country_of_origin="France",
            government_warning=REQUIRED_WARNING.replace("WARNING:", "WARNING", 1),
        ),
    )

    assert result.overall_verdict is VerificationVerdict.NEEDS_REVIEW
    assert len(result.results) == 7
    assert [field.field for field in result.results] == [
        "brand_name",
        "class_type",
        "producer",
        "country_of_origin",
        "abv",
        "net_contents",
        "government_warning",
    ]
    assert sum(field.status is FieldStatus.FAIL for field in result.results) == 3
