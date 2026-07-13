from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

import backend.app.main as main
from backend.app.verification.vision import VisionConfigurationError


class _ReadyVisionService:
    def __init__(self) -> None:
        self.checks = 0

    def verify_configured_model(self) -> None:
        self.checks += 1


def test_startup_runs_model_readiness_check(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _ReadyVisionService()
    monkeypatch.setattr(main, "_cached_vision_service", lambda: service)

    with TestClient(main.app):
        assert service.checks == 1


def test_startup_rejects_missing_openai_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    main._cached_vision_service.cache_clear()

    with pytest.raises(VisionConfigurationError):
        with TestClient(main.app):
            pass

    main._cached_vision_service.cache_clear()


def test_cached_factory_reuses_one_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    service = _ReadyVisionService()

    def create_service() -> _ReadyVisionService:
        nonlocal calls
        calls += 1
        return service

    main._cached_vision_service.cache_clear()
    monkeypatch.setattr(main.OpenAIVisionService, "from_env", create_service)

    factory = main.get_vision_service()
    assert factory() is service
    assert factory() is service
    assert calls == 1

    main._cached_vision_service.cache_clear()
