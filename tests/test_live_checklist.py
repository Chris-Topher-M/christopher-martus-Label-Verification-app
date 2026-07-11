from __future__ import annotations

from typing import Any

from scripts import run_live_checklist


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


class _VerifyClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response(self.payload)


def test_live_checklist_posts_the_current_single_label_contract() -> None:
    client = _VerifyClient(
        {
            "overall_verdict": "APPROVED",
            "results": [],
            "latency_ms": 12,
        }
    )

    result = run_live_checklist.check_valid_label(client, "https://example.test")

    assert result.passed is True
    assert client.calls[0]["data"] == run_live_checklist.application_form()
    assert set(client.calls[0]["data"]) == {
        "brand_name",
        "class_type",
        "producer",
        "country_of_origin",
        "abv",
        "net_contents",
        "government_warning",
    }


def test_live_checklist_reads_current_result_and_verdict_keys() -> None:
    body = {
        "overall_verdict": "NEEDS_REVIEW",
        "results": [{"field": "abv", "status": "FAIL"}],
    }

    assert run_live_checklist.field_result(body, "abv") == {"field": "abv", "status": "FAIL"}
    assert "verdict=NEEDS_REVIEW" in run_live_checklist.verdict_detail(_Response(body), body)
