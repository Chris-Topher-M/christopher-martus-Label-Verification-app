from __future__ import annotations

import asyncio
from collections.abc import Iterator
from io import BytesIO
import socket
import threading
import time

import pytest
import uvicorn
from PIL import Image
from playwright.sync_api import Locator, Page, expect

from backend.app.main import app, get_vision_service
from backend.app.verification.vision import MockVisionService


REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN "
    "SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF "
    "THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES "
    "IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY "
    "CAUSE HEALTH PROBLEMS."
)


class DelayedMockVisionService(MockVisionService):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    async def extract_label(self, image_bytes: bytes, content_type: str | None = None):  # type: ignore[no-untyped-def]
        self.started.set()
        if not await asyncio.to_thread(self.release.wait, 10):
            raise TimeoutError("The browser test did not release the vision service.")
        return await super().extract_label(image_bytes, content_type)


@pytest.fixture
def browser_app() -> Iterator[tuple[str, DelayedMockVisionService]]:
    service = DelayedMockVisionService()
    app.dependency_overrides[get_vision_service] = lambda: lambda: service

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        app.dependency_overrides.clear()
        pytest.fail("The browser test server did not start.")

    try:
        yield f"http://127.0.0.1:{port}", service
    finally:
        service.release.set()
        server.should_exit = True
        thread.join(timeout=5)
        app.dependency_overrides.clear()


def test_single_label_submit_keeps_form_data_when_controls_become_busy(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, service = browser_app
    page.goto(base_url)
    page.set_input_files(
        "#image",
        {
            "name": "label.png",
            "mimeType": "image/png",
            "buffer": _image_bytes(),
        },
    )
    page.locator("#brand_name").fill("Acme Reserve")
    page.locator("#class_type").fill("Red Wine")
    page.locator("#producer").fill("Acme Winery, LLC")
    page.locator("#country_of_origin").fill("United States")
    page.locator("#abv").fill("13.5%")
    page.locator("#net_contents").fill("750 mL")
    page.locator("#government_warning").fill(REQUIRED_WARNING)

    page.locator("#submit-button").click()
    assert service.started.wait(timeout=2), "The submitted form did not reach the vision service."
    expect(page.locator("#brand_name")).to_be_disabled()
    expect(page.locator("#submit-button")).to_be_disabled()

    service.release.set()

    expect(page.locator("#results .verdict-title")).to_have_text("APPROVED")
    expect(page.locator("#brand_name")).to_be_enabled()
    assert service.calls == [(_image_bytes(), "image/png")]


def test_invalid_numeric_fields_are_blocked_before_fetch(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, service = browser_app
    page.goto(base_url)
    _select_single_image(page)
    _fill_single_fields(page, abv="forty-five", net_contents="one bottle")

    page.locator("#submit-button").click()

    expect(page.locator("#error-box")).to_contain_text(
        "Alcohol by Volume: Enter a number such as 13.5%, 13.5, 0.135, or 27 proof."
    )
    expect(page.locator("#error-box")).to_contain_text(
        "Net Contents: Enter a positive amount with a unit"
    )
    assert service.started.is_set() is False


def test_numeric_validation_accepts_supported_application_formats(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, _ = browser_app
    page.goto(base_url)

    for value in ("13.5", "13.5%", "0.135", "27 proof"):
        assert page.evaluate("value => validateAbv(value)", value) is None
    for value in ("750 mL", "0.75 L", "25 fl oz"):
        assert page.evaluate("value => validateNetContents(value)", value) is None


def test_invalid_batch_numeric_fields_are_blocked_before_fetch(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, service = browser_app
    page.goto(base_url)
    page.locator("#batch-mode-button").click()
    _select_batch_images(page, "label.png")
    _fill_batch_card(page.locator(".batch-label-card").nth(0), abv="forty-five")

    page.locator("#batch-submit-button").click()

    expect(page.locator("#batch-error-box")).to_contain_text(
        "label.png - Alcohol by Volume: Enter a number such as 13.5%"
    )
    assert service.started.is_set() is False


def test_batch_copy_down_preserves_independent_values_and_survives_removal(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, _ = browser_app
    page.goto(base_url)
    page.locator("#batch-mode-button").click()
    page.set_input_files(
        "#batch-images",
        [
            {"name": name, "mimeType": "image/png", "buffer": _image_bytes()}
            for name in ("first.png", "second.png", "third.png")
        ],
    )

    cards = page.locator(".batch-label-card")
    expect(cards).to_have_count(3)
    source_values = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": "13.5%",
        "net_contents": "750 mL",
        "government_warning": REQUIRED_WARNING,
    }
    for field, value in source_values.items():
        cards.nth(0).locator(f'[data-field="{field}"]').fill(value)

    cards.nth(1).locator(".copy-button").click()
    for field, value in source_values.items():
        expect(cards.nth(1).locator(f'[data-field="{field}"]')).to_have_value(value)

    cards.nth(1).locator('[data-field="brand_name"]').fill("Second Brand")
    expect(cards.nth(0).locator('[data-field="brand_name"]')).to_have_value("Acme Reserve")
    cards.nth(2).locator(".copy-button").click()

    cards.nth(0).locator(".remove-button").click()
    expect(cards).to_have_count(2)
    expect(cards.nth(0).locator('[data-field="brand_name"]')).to_have_value("Second Brand")
    expect(cards.nth(0).locator('[data-field="government_warning"]')).to_have_value(REQUIRED_WARNING)

    cards.nth(1).locator(".copy-button").click()
    expect(cards.nth(1).locator('[data-field="brand_name"]')).to_have_value("Second Brand")
    expect(page.locator("#status-line")).to_contain_text("Copied details from second.png to third.png.")


def test_first_slow_request_shows_cold_start_message_and_stops_after_five_seconds(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, service = browser_app
    page.goto(base_url)
    _select_single_image(page)
    _fill_single_fields(page)

    page.locator("#submit-button").click()
    assert service.started.wait(timeout=2), "The submitted form did not reach the vision service."
    expect(page.locator("#loading-message")).to_be_visible()
    expect(page.locator("#cold-start-message")).to_be_visible(timeout=4_000)
    expect(page.locator("#loading-message")).to_be_hidden()
    expect(page.locator("#error-box")).to_contain_text(
        "The server may still be waking up. Wait a moment and try again.",
        timeout=3_000,
    )
    expect(page.locator("#submit-button")).to_be_enabled()
    expect(page.locator("#cold-start-message")).to_be_hidden()


def test_first_slow_batch_shows_cold_start_message_and_stops_after_five_seconds(
    page: Page,
    browser_app: tuple[str, DelayedMockVisionService],
) -> None:
    base_url, service = browser_app
    page.goto(base_url)
    page.locator("#batch-mode-button").click()
    _select_batch_images(page, "label.png")
    _fill_batch_card(page.locator(".batch-label-card").nth(0))

    page.locator("#batch-submit-button").click()
    assert service.started.wait(timeout=2), "The submitted batch did not reach the vision service."
    expect(page.locator("#batch-loading-message")).to_be_visible()
    expect(page.locator("#batch-progress-panel")).to_be_visible(timeout=2_000)
    expect(page.locator("#batch-cold-start-message")).to_be_visible(timeout=4_000)
    expect(page.locator("#batch-progress-panel")).to_be_hidden()
    expect(page.locator("#batch-error-box")).to_contain_text(
        "The server may still be waking up. Wait a moment and try again.",
        timeout=3_000,
    )
    expect(page.locator("#batch-submit-button")).to_be_enabled()
    expect(page.locator("#batch-cold-start-message")).to_be_hidden()


def _select_single_image(page: Page) -> None:
    page.set_input_files(
        "#image",
        {
            "name": "label.png",
            "mimeType": "image/png",
            "buffer": _image_bytes(),
        },
    )


def _select_batch_images(page: Page, *names: str) -> None:
    page.set_input_files(
        "#batch-images",
        [
            {"name": name, "mimeType": "image/png", "buffer": _image_bytes()}
            for name in names
        ],
    )


def _fill_single_fields(
    page: Page,
    *,
    abv: str = "13.5%",
    net_contents: str = "750 mL",
) -> None:
    page.locator("#brand_name").fill("Acme Reserve")
    page.locator("#class_type").fill("Red Wine")
    page.locator("#producer").fill("Acme Winery, LLC")
    page.locator("#country_of_origin").fill("United States")
    page.locator("#abv").fill(abv)
    page.locator("#net_contents").fill(net_contents)
    page.locator("#government_warning").fill(REQUIRED_WARNING)


def _fill_batch_card(
    card: Locator,
    *,
    abv: str = "13.5%",
    net_contents: str = "750 mL",
) -> None:
    values = {
        "brand_name": "Acme Reserve",
        "class_type": "Red Wine",
        "producer": "Acme Winery, LLC",
        "country_of_origin": "United States",
        "abv": abv,
        "net_contents": net_contents,
        "government_warning": REQUIRED_WARNING,
    }
    for field, value in values.items():
        card.locator(f'[data-field="{field}"]').fill(value)


def _image_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), (120, 30, 60))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
