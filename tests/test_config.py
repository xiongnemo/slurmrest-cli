"""Unit tests for configuration resolution. No network, no cluster."""

import pytest

from slurmrest import config


def test_flag_beats_environment(monkeypatch):
    monkeypatch.setenv(config.ENV_URL, "http://from-env")
    cfg = config.load(url="http://from-flag")
    assert cfg.url == "http://from-flag"


def test_environment_is_used_when_no_flag(monkeypatch):
    monkeypatch.setenv(config.ENV_URL, "http://from-env/")
    cfg = config.load()
    assert cfg.url == "http://from-env"  # trailing slash stripped


def test_missing_url_is_an_error(monkeypatch, tmp_path):
    monkeypatch.delenv(config.ENV_URL, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(config.ConfigError):
        config.load()


def test_slurm_jwt_is_accepted_as_token(monkeypatch):
    monkeypatch.setenv(config.ENV_URL, "http://x")
    monkeypatch.delenv("SLURMREST_TOKEN", raising=False)
    monkeypatch.setenv("SLURM_JWT", "abc123")
    assert config.load().token == "abc123"


def test_token_header_is_added():
    cfg = config.Config(url="http://x", token="t")
    assert cfg.auth_headers["X-SLURM-USER-TOKEN"] == "t"


def test_extra_headers_parse():
    cfg = config.load(url="http://x", headers=["A: 1", "B: 2"])
    assert cfg.headers == {"A": "1", "B": "2"}


def test_malformed_header_rejected():
    with pytest.raises(ValueError):
        config.load(url="http://x", headers=["no-colon"])


def test_profile_from_config_file(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "slurmrest"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        '[staging]\nurl = "http://staging"\ntoken = "tok"\n'
        '[staging.headers]\n"X-Gate" = "v"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(config.ENV_URL, raising=False)
    monkeypatch.delenv("SLURM_JWT", raising=False)
    monkeypatch.delenv("SLURMREST_TOKEN", raising=False)
    cfg = config.load(profile="staging")
    assert cfg.url == "http://staging"
    assert cfg.token == "tok"
    assert cfg.headers["X-Gate"] == "v"
