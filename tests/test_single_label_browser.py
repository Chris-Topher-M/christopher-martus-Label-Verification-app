from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
import socket
import threading
import time

import pytest
import uvicorn
from PIL import Image
from playwright.sync_api import Page, expect

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

    def extract_label(self, image_bytes: bytes, content_type: str | None = None):  # type: ignore[no-untyped-def]
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("The browser test did not release the vision service.")
        return super().extract_label(image_bytes, content_type)


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


def _image_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), (120, 30, 60))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
