"""Tier 2.7 -- HTTP transport overrides (proxy / User-Agent / headers / cookies).

Review item 7 (CODE_REVIEW_2026-08-27): "robots.txt + politeness config,
per-host concurrency caps, proxy support, custom headers/cookies for
authenticated sources." robots/politeness (S4 + ``domain_delay``) and per-host
concurrency (S5) already exist; this adds proxy / User-Agent / custom headers
/ cookies for the static (Rust) fetch path.

The overrides are process-level: they are baked into the lazily-built shared
reqwest client at first use via the Rust ``configure_http`` binding. All tests
here are deterministic -- none performs a live fetch, so the shared client is
never built and no network is touched.
"""
from __future__ import annotations

from gossamer import agent_tools
from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.mcp_server import _config_from_env, _env_json_dict


def _toolbox(tmp_path, **config_kwargs) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


class TestToolboxConfigFields:
    def test_defaults_empty(self):
        c = ToolboxConfig(cache_dir="/tmp/x")
        assert c.http_proxy is None
        assert c.user_agent is None
        assert c.custom_headers == {}
        assert c.cookies == {}

    def test_accepts_overrides(self):
        c = ToolboxConfig(
            cache_dir="/tmp/x",
            http_proxy="http://proxy:8080",
            user_agent="agent/1.0",
            custom_headers={"Authorization": "Bearer t"},
            cookies={"session": "abc"},
        )
        assert c.http_proxy == "http://proxy:8080"
        assert c.user_agent == "agent/1.0"
        assert c.custom_headers == {"Authorization": "Bearer t"}
        assert c.cookies == {"session": "abc"}

    def test_dict_fields_isolated_per_instance(self):
        a = ToolboxConfig(cache_dir="/tmp/a", custom_headers={"X": "1"})
        b = ToolboxConfig(cache_dir="/tmp/b")
        a.custom_headers["Y"] = "2"
        assert "Y" not in b.custom_headers
        assert b.custom_headers == {}


class TestConfigureHttpWiring:
    def test_not_called_when_unset(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            agent_tools, "_configure_http", lambda *a: calls.append(a)
        )
        _toolbox(tmp_path)
        assert calls == []

    def test_called_with_overrides(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            agent_tools, "_configure_http", lambda *a: calls.append(a)
        )
        _toolbox(
            tmp_path,
            http_proxy="http://proxy:8080",
            user_agent="agent/1.0",
            custom_headers={"Authorization": "Bearer t"},
            cookies={"session": "abc"},
        )
        assert len(calls) == 1
        proxy, ua, headers, cookies = calls[0]
        assert proxy == "http://proxy:8080"
        assert ua == "agent/1.0"
        assert headers == [("Authorization", "Bearer t")]
        assert cookies == [("session", "abc")]

    def test_called_when_only_headers_set(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            agent_tools, "_configure_http", lambda *a: calls.append(a)
        )
        _toolbox(tmp_path, custom_headers={"X": "1"})
        assert len(calls) == 1
        assert calls[0][0] is None  # no proxy
        assert calls[0][1] is None  # no user agent
        assert calls[0][2] == [("X", "1")]
        assert calls[0][3] == []


class TestRustConfigureHttp:
    def test_smoke_callable_and_idempotent(self):
        # The real Rust binding: setting overrides must not raise and it is
        # safe to call more than once (last non-empty value wins). No fetch
        # happens here, so the shared client is never built and no bogus
        # proxy is left behind.
        agent_tools._configure_http(None, None, [("X-Gossamer-Test", "1")], [])
        agent_tools._configure_http(None, None, [], [])


class TestEnvKnobs:
    def test_env_json_dict(self, monkeypatch):
        monkeypatch.delenv("GOSSAMER_CUSTOM_HEADERS", raising=False)
        assert _env_json_dict("GOSSAMER_CUSTOM_HEADERS") == {}
        monkeypatch.setenv(
            "GOSSAMER_CUSTOM_HEADERS", '{"Authorization": "Bearer t"}'
        )
        assert _env_json_dict("GOSSAMER_CUSTOM_HEADERS") == {
            "Authorization": "Bearer t"
        }
        monkeypatch.setenv("GOSSAMER_CUSTOM_HEADERS", "not json")
        assert _env_json_dict("GOSSAMER_CUSTOM_HEADERS") == {}
        monkeypatch.setenv("GOSSAMER_CUSTOM_HEADERS", '["a", "b"]')
        assert _env_json_dict("GOSSAMER_CUSTOM_HEADERS") == {}
        monkeypatch.setenv("GOSSAMER_CUSTOM_HEADERS", '{"a": 1}')
        assert _env_json_dict("GOSSAMER_CUSTOM_HEADERS") == {"a": "1"}

    def test_config_from_env_wires_transport(self, monkeypatch):
        for var in (
            "GOSSAMER_HTTP_PROXY",
            "GOSSAMER_USER_AGENT",
            "GOSSAMER_CUSTOM_HEADERS",
            "GOSSAMER_COOKIES",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = _config_from_env()
        assert cfg.http_proxy is None
        assert cfg.user_agent is None
        assert cfg.custom_headers == {}
        assert cfg.cookies == {}

        monkeypatch.setenv("GOSSAMER_HTTP_PROXY", "http://proxy:8080")
        monkeypatch.setenv("GOSSAMER_USER_AGENT", "agent/1.0")
        monkeypatch.setenv("GOSSAMER_CUSTOM_HEADERS", '{"Authorization": "Bearer t"}')
        monkeypatch.setenv("GOSSAMER_COOKIES", '{"session": "abc"}')
        cfg = _config_from_env()
        assert cfg.http_proxy == "http://proxy:8080"
        assert cfg.user_agent == "agent/1.0"
        assert cfg.custom_headers == {"Authorization": "Bearer t"}
        assert cfg.cookies == {"session": "abc"}
