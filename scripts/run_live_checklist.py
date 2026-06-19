from __future__ import annotations

import argparse
from dataclasses import dataclass
from io import BytesIO
import json
import statistics
import sys
import time
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont


REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN "
    "SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF "
    "THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES "
    "IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY "
    "CAUSE HEALTH PROBLEMS."
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    latency_ms: int | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 6 checklist against a deployed URL.")
    parser.add_argument("base_url", help="Deployed base URL, for example https://example.onrender.com")
    parser.add_argument("--speed-runs", type=int, default=5, help="Number of valid-label latency runs.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    with httpx.Client(timeout=45) as client:
        results = run_checklist(client, base_url, max(args.speed_runs, 1))

    print(json.dumps([result.__dict__ for result in results], indent=2))
    failed = [result for result in results if not result.passed]
    if failed:
        print("\nFailed checks:", file=sys.stderr)
        for result in failed:
            print(f"- {result.name}: {result.detail}", file=sys.stderr)
        return 1
    return 0


def run_checklist(client: httpx.Client, base_url: str, speed_runs: int) -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(check_health(client, base_url))
    results.append(check_valid_label(client, base_url))
    results.append(check_mismatch(client, base_url))
    results.append(check_case_only(client, base_url))
    results.append(check_abv_units(client, base_url))
    results.append(check_warning_variants(client, base_url))
    results.append(check_imperfect_image(client, base_url))
    results.append(check_wrong_file_type(client, base_url))
    results.append(check_empty_submit(client, base_url))
    results.append(check_batch_summary(client, base_url))
    results.append(check_single_label_speed(client, base_url, speed_runs))
    return results


def check_health(client: httpx.Client, base_url: str) -> CheckResult:
    response = client.get(f"{base_url}/health")
    passed = response.status_code == 200 and response.json().get("status") == "healthy"
    return CheckResult("health", passed, f"HTTP {response.status_code}")


def check_valid_label(client: httpx.Client, base_url: str) -> CheckResult:
    response, elapsed = post_verify(client, base_url, image_bytes(), application_form())
    body = safe_json(response)
    passed = response.status_code == 200 and body.get("verdict") == "PASS"
    return CheckResult("valid label", passed, verdict_detail(response, body), elapsed)


def check_mismatch(client: httpx.Client, base_url: str) -> CheckResult:
    form = application_form(brand_name="Mountain Cellars")
    response, elapsed = post_verify(client, base_url, image_bytes(), form)
    body = safe_json(response)
    brand = field_result(body, "brand_name")
    passed = (
        response.status_code == 200
        and body.get("verdict") == "NEEDS_REVIEW"
        and brand.get("status") == "FAIL"
    )
    return CheckResult("mismatches", passed, verdict_detail(response, body), elapsed)


def check_case_only(client: httpx.Client, base_url: str) -> CheckResult:
    form = application_form(brand_name="ACME RESERVE")
    response, elapsed = post_verify(client, base_url, image_bytes(), form)
    body = safe_json(response)
    brand = field_result(body, "brand_name")
    passed = response.status_code == 200 and brand.get("status") == "PASS"
    return CheckResult("case-only fuzzy field", passed, verdict_detail(response, body), elapsed)


def check_abv_units(client: httpx.Client, base_url: str) -> CheckResult:
    form = application_form(alcohol_by_volume="0.135", net_contents="0.75 L")
    response, elapsed = post_verify(client, base_url, image_bytes(), form)
    body = safe_json(response)
    abv = field_result(body, "alcohol_by_volume")
    net = field_result(body, "net_contents")
    passed = response.status_code == 200 and abv.get("status") == "PASS" and net.get("status") == "PASS"
    return CheckResult("ABV and units normalization", passed, verdict_detail(response, body), elapsed)


def check_warning_variants(client: httpx.Client, base_url: str) -> CheckResult:
    correct_response, correct_elapsed = post_verify(client, base_url, image_bytes(), application_form())
    correct_body = safe_json(correct_response)
    correct_warning = field_result(correct_body, "government_warning")

    wrong_caps_response, _ = post_verify(
        client,
        base_url,
        image_bytes(warning=REQUIRED_WARNING.title()),
        application_form(),
    )
    wrong_caps_body = safe_json(wrong_caps_response)
    wrong_caps_warning = field_result(wrong_caps_body, "government_warning")

    missing_response, _ = post_verify(
        client,
        base_url,
        image_bytes(warning=None),
        application_form(),
    )
    missing_body = safe_json(missing_response)
    missing_warning = field_result(missing_body, "government_warning")

    passed = (
        correct_response.status_code == 200
        and correct_warning.get("status") == "PASS"
        and wrong_caps_response.status_code == 200
        and wrong_caps_warning.get("status") == "FAIL"
        and missing_response.status_code == 200
        and missing_warning.get("status") == "FAIL"
    )
    detail = (
        f"correct={correct_warning.get('status')} "
        f"wrong_caps={wrong_caps_warning.get('status')} missing={missing_warning.get('status')}"
    )
    return CheckResult("warning exact/missing/wrong-caps", passed, detail, correct_elapsed)


def check_imperfect_image(client: httpx.Client, base_url: str) -> CheckResult:
    response, elapsed = post_verify(
        client,
        base_url,
        image_bytes(blur=True, warning=None),
        application_form(),
    )
    body = safe_json(response)
    passed = response.status_code == 200 and body.get("verdict") == "NEEDS_REVIEW"
    return CheckResult("imperfect readable image", passed, verdict_detail(response, body), elapsed)


def check_wrong_file_type(client: httpx.Client, base_url: str) -> CheckResult:
    response, elapsed = post_verify(
        client,
        base_url,
        b"not an image",
        application_form(),
        filename="label.txt",
        content_type="text/plain",
    )
    body = safe_json(response)
    passed = response.status_code == 415 and "JPG, PNG, or WebP" in json.dumps(body)
    return CheckResult("wrong file type", passed, f"HTTP {response.status_code}: {body}", elapsed)


def check_empty_submit(client: httpx.Client, base_url: str) -> CheckResult:
    started_at = time.perf_counter()
    response = client.post(f"{base_url}/verify", data={})
    elapsed = int((time.perf_counter() - started_at) * 1000)
    body = safe_json(response)
    passed = response.status_code == 422 and "required" in json.dumps(body).lower()
    return CheckResult("empty submit", passed, f"HTTP {response.status_code}: {body}", elapsed)


def check_batch_summary(client: httpx.Client, base_url: str) -> CheckResult:
    first = image_bytes()
    second = b"not an image"
    items = [
        {"client_id": "label-1", **application_form()},
        {"client_id": "label-2", **application_form()},
    ]
    files = [
        ("images", ("front-1.png", first, "image/png")),
        ("images", ("front-2.txt", second, "text/plain")),
    ]
    started_at = time.perf_counter()
    response = client.post(f"{base_url}/verify/batch", data={"items": json.dumps(items)}, files=files)
    elapsed = int((time.perf_counter() - started_at) * 1000)
    body = safe_json(response)
    summary = body.get("summary", {})
    passed = (
        response.status_code == 200
        and summary.get("total") == 2
        and summary.get("needs_review", 0) >= 1
        and len(body.get("items", [])) == 2
    )
    return CheckResult("batch summary", passed, f"HTTP {response.status_code}: {summary}", elapsed)


def check_single_label_speed(client: httpx.Client, base_url: str, speed_runs: int) -> CheckResult:
    latencies: list[int] = []
    verdicts: list[str] = []
    for _ in range(speed_runs):
        response, elapsed = post_verify(client, base_url, image_bytes(), application_form())
        latencies.append(elapsed)
        verdicts.append(str(safe_json(response).get("verdict")))

    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))]
    maximum = max(latencies)
    mean = round(statistics.mean(latencies), 1)
    passed = maximum <= 5000 and p95 <= 4500 and all(verdict == "PASS" for verdict in verdicts)
    detail = f"runs={latencies} p95={p95} max={maximum} mean={mean} verdicts={verdicts}"
    return CheckResult("single-label speed", passed, detail, maximum)


def post_verify(
    client: httpx.Client,
    base_url: str,
    image: bytes,
    form: dict[str, str],
    *,
    filename: str = "label.png",
    content_type: str = "image/png",
) -> tuple[httpx.Response, int]:
    started_at = time.perf_counter()
    response = client.post(
        f"{base_url}/verify",
        data=form,
        files={"image": (filename, image, content_type)},
    )
    elapsed = int((time.perf_counter() - started_at) * 1000)
    return response, elapsed


def image_bytes(*, warning: str | None = REQUIRED_WARNING, blur: bool = False) -> bytes:
    image = Image.new("RGB", (1500, 1900), "white")
    draw = ImageDraw.Draw(image)
    font = load_font(42)
    small_font = load_font(34)
    y = 70
    for line in [
        "Acme Reserve",
        "Red Wine",
        "Acme Winery, LLC",
        "United States",
        "13.5% Alc./Vol.",
        "750 mL",
    ]:
        draw.text((80, y), line, fill="black", font=font)
        y += 86
    if warning is not None:
        y += 40
        for line in wrap_text(warning, 54):
            draw.text((80, y), line, fill="black", font=small_font)
            y += 52
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(radius=2.2))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def load_font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_text(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if len(candidate) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def application_form(**overrides: str) -> dict[str, str]:
    form = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer_name": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "alcohol_by_volume": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    form.update(overrides)
    return form


def safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"raw": response.text[:500]}
    return payload if isinstance(payload, dict) else {"raw": payload}


def field_result(body: dict[str, Any], field_name: str) -> dict[str, Any]:
    fields = body.get("fields", [])
    if not isinstance(fields, list):
        return {}
    for field in fields:
        if isinstance(field, dict) and field.get("field") == field_name:
            return field
    return {}


def verdict_detail(response: httpx.Response, body: dict[str, Any]) -> str:
    failed = [
        f"{field.get('field')}={field.get('status')}"
        for field in body.get("fields", [])
        if isinstance(field, dict) and field.get("status") != "PASS"
    ]
    suffix = f" failed_fields={failed}" if failed else ""
    return f"HTTP {response.status_code} verdict={body.get('verdict')}{suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
