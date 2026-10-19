"""Configuration order: env vars, PFM_ENV_FILE, legacy file, defaults."""

from pfm_mcp import config


def _write(path, text):
    path.write_text(text)
    return str(path)


def test_env_vars_win(tmp_path):
    named = _write(
        tmp_path / "n.env", "PORTF_SERVER_URL=http://file\nSERVER_API_KEY=f\n"
    )
    got = config.resolve(
        {"PFM_URL": "http://env", "PFM_API_KEY": "e", "PFM_ENV_FILE": named},
        legacy_path=str(tmp_path / "missing"),
    )
    assert got == config.Settings("http://env", "e")


def test_named_env_file_beats_legacy(tmp_path):
    named = _write(
        tmp_path / "n.env", "PORTF_SERVER_URL=http://file\nSERVER_API_KEY=f\n"
    )
    legacy = _write(
        tmp_path / "l.env", "PORTF_SERVER_URL=http://old\nSERVER_API_KEY=o\n"
    )
    got = config.resolve({"PFM_ENV_FILE": named}, legacy_path=legacy)
    assert got == config.Settings("http://file", "f")


def test_legacy_file_is_used_last(tmp_path):
    legacy = _write(
        tmp_path / "l.env", "# comment\nPORTF_SERVER_URL=http://old\nSERVER_API_KEY=o\n"
    )
    assert config.resolve({}, legacy_path=legacy) == config.Settings("http://old", "o")


def test_url_and_key_resolve_independently(tmp_path):
    legacy = _write(tmp_path / "l.env", "SERVER_API_KEY=o\n")
    got = config.resolve({"PFM_URL": "http://env"}, legacy_path=legacy)
    assert got == config.Settings("http://env", "o")


def test_defaults_and_stderr_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PFM_URL", raising=False)
    monkeypatch.delenv("PFM_API_KEY", raising=False)
    monkeypatch.delenv("PFM_ENV_FILE", raising=False)
    monkeypatch.setattr(config, "LEGACY_ENV_FILE", str(tmp_path / "missing"))
    monkeypatch.setattr(
        config, "resolve", lambda: config.Settings(config.DEFAULT_URL, "")
    )
    config.settings.cache_clear()
    try:
        got = config.settings()
        captured = capsys.readouterr()
    finally:
        config.settings.cache_clear()
    assert got.url == "http://127.0.0.1:8000" and got.api_key == ""
    assert captured.out == ""
    assert "PFM_API_KEY" in captured.err


def test_resolve_defaults_when_nothing_is_set(tmp_path):
    got = config.resolve({}, legacy_path=str(tmp_path / "missing"))
    assert got == config.Settings("http://127.0.0.1:8000", "")
