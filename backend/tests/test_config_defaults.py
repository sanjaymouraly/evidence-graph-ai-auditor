"""Configuration contract: secure defaults that must not silently regress.

The test suite sets `EGA_ALLOW_LOCAL_PROVIDER_URLS=true` so it can exercise Ollama-style
endpoints. That means an ordinary test can no longer observe the shipped default, so the
default itself is asserted here, together with the URL guard it controls.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.providers import ProviderError, validate_base_url


def test_shipped_default_blocks_local_provider_urls():
    assert Settings.model_fields["allow_local_provider_urls"].default is False
    assert Settings.model_fields["require_https"].default is False
    assert Settings.model_fields["max_upload_mb"].default == 25


def test_local_urls_are_rejected_unless_explicitly_allowed():
    with pytest.raises(ProviderError, match="Private/local provider URLs are disabled"):
        validate_base_url("http://127.0.0.1:11434", allow_local=False)
    with pytest.raises(ProviderError, match="Private/local provider URLs are disabled"):
        validate_base_url("http://localhost:11434", allow_local=False)
    assert validate_base_url("http://127.0.0.1:11434", allow_local=True) == "http://127.0.0.1:11434"


def test_metadata_and_unroutable_hosts_are_always_blocked():
    for url in ("http://169.254.169.254/latest", "http://metadata.google.internal/",
                "http://0.0.0.0:8080", "ftp://example.com"):
        with pytest.raises(ProviderError):
            validate_base_url(url, allow_local=True)


def test_public_https_endpoint_is_accepted_and_normalised():
    assert validate_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"


def test_hostname_resolving_to_private_range_is_blocked(monkeypatch):
    """A public-looking hostname must not bypass the guard by resolving to localhost."""
    from app.services import providers as provider_module

    monkeypatch.setattr(provider_module.socket, "getaddrinfo",
                        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(ProviderError, match="Private/local provider URLs are disabled"):
        validate_base_url("https://internal.example.test/v1", allow_local=False)


def test_hostname_resolving_to_link_local_range_is_blocked(monkeypatch):
    from app.services import providers as provider_module

    monkeypatch.setattr(provider_module.socket, "getaddrinfo",
                        lambda *args, **kwargs: [(2, 1, 6, "", ("169.254.169.254", 0))])
    with pytest.raises(ProviderError, match="Private/local provider URLs are disabled"):
        validate_base_url("https://metadata.example.test/v1", allow_local=False)


def test_production_refuses_to_start_without_a_secret():
    """A missing secret used to surface as a 500 on every login while health said 'ok'."""
    from app.config import Settings

    broken = Settings(environment="production", secret_key="")
    problems = broken.startup_errors()
    assert any("EGA_SECRET_KEY" in p for p in problems)

    placeholder = Settings(environment="production", secret_key="change-me-in-production")
    assert any("EGA_SECRET_KEY" in p for p in placeholder.startup_errors())

    good = Settings(environment="production", secret_key="a" * 48, require_https=True,
                    allow_local_provider_urls=False)
    assert good.startup_errors() == []
    assert good.startup_warnings() == []


def test_development_does_not_require_a_secret():
    from app.config import Settings

    assert Settings(environment="development", secret_key="").startup_errors() == []
