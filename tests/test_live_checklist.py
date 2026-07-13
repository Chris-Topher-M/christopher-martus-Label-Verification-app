from __future__ import annotations

from typing import Any

from scripts import run_live_checklist


class _Response:
    status_code = 200

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.text = ""
        self.headers = headers or {}
        self.status_code = status_code

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


def test_server_timing_parser_reads_app_duration() -> None:
    response = _Response({}, headers={"server-timing": "cache;dur=2, app;dur=1234.4"})

    assert run_live_checklist.server_timing_ms(response) == 1234


def test_speed_check_warms_up_and_reports_official_statistics() -> None:
    client = _SpeedClient()

    result = run_live_checklist.check_single_label_speed(
        client,
        "https://example.test",
        run_live_checklist.MIN_OFFICIAL_SPEED_RUNS,
    )

    assert result.passed is True
    assert len(client.calls) == run_live_checklist.MIN_OFFICIAL_SPEED_RUNS + 1
    assert "warmup_status=200" in result.detail
    assert "sample_count=20" in result.detail
    assert "server_p50=1200" in result.detail
    assert "timeout_count=0" in result.detail


def test_speed_check_rejects_too_few_samples_for_official_p95() -> None:
    result = run_live_checklist.check_single_label_speed(
        _SpeedClient(),
        "https://example.test",
        run_live_checklist.MIN_OFFICIAL_SPEED_RUNS - 1,
    )

    assert result.passed is False
    assert "sample_count=19" in result.detail


def test_speed_check_counts_timeouts_and_fails_acceptance() -> None:
    result = run_live_checklist.check_single_label_speed(
        _SpeedClient(timeout_on_first_measured_run=True),
        "https://example.test",
        run_live_checklist.MIN_OFFICIAL_SPEED_RUNS,
    )

    assert result.passed is False
    assert "timeout_count=1" in result.detail
    assert "504" in result.detail


class _SpeedClient:
    def __init__(self, *, timeout_on_first_measured_run: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.timeout_on_first_measured_run = timeout_on_first_measured_run

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if self.timeout_on_first_measured_run and len(self.calls) == 2:
            return _Response(
                {"error": {"code": "VISION_TIMEOUT"}},
                headers={"server-timing": "app;dur=4500"},
                status_code=504,
            )
        return _Response(
            {"overall_verdict": "APPROVED", "results": [], "latency_ms": 1200},
            headers={"server-timing": "app;dur=1200"},
        )
