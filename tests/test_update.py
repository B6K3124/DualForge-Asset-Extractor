from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import dualforge.cli_commands.update as cmd_update
import dualforge.update as upd
from dualforge.update import NoReleasesError, UpdateError
from dualforge.version import __version__


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


def _fake_get(payload, status=200):
    def fake_get(url, timeout=None):
        return _Resp(payload, status)

    return fake_get


# --- version parsing / comparison -------------------------------------------


def test_parse_version_strips_v_and_pads():
    assert upd._parse_version("v0.3.1") == (0, 3, 1)
    assert upd._parse_version("0.4") == (0, 4)
    assert upd._parse_version("1.2.3rc1") == (1, 2, 3)


def test_parse_version_rejects_garbage():
    with pytest.raises(UpdateError):
        upd._parse_version("latest")
    with pytest.raises(UpdateError):
        upd._parse_version("")


def test_compare_versions_holds_user_semantics():
    assert upd.compare_versions("0.3.1", "0.3.0") < 0  # installed older
    assert upd.compare_versions("0.3.0", "0.3.0") == 0  # same
    assert upd.compare_versions("0.3.0", "0.4.0") > 0  # installed newer


# --- fetching ---------------------------------------------------------------


def test_fetch_latest_version_ok(monkeypatch):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": "v0.3.1"})
    )
    assert upd.fetch_latest_version("http://example.test/latest") == "0.3.1"


def test_fetch_latest_version_no_v(monkeypatch):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": "0.3.0"})
    )
    assert upd.fetch_latest_version() == "0.3.0"


def test_fetch_latest_version_no_releases(monkeypatch):
    monkeypatch.setattr("dualforge.update.requests.get", _fake_get({}, status=404))
    with pytest.raises(NoReleasesError):
        upd.fetch_latest_version("http://example.test/latest")


def test_fetch_latest_version_http_error(monkeypatch):
    monkeypatch.setattr("dualforge.update.requests.get", _fake_get({}, status=500))
    with pytest.raises(UpdateError) as excinfo:
        upd.fetch_latest_version("http://example.test/latest")
    assert not isinstance(excinfo.value, NoReleasesError)


def test_fetch_latest_version_request_error(monkeypatch):
    def boom(url, timeout=None):
        import requests

        raise requests.RequestException("offline")

    monkeypatch.setattr("dualforge.update.requests.get", boom)
    with pytest.raises(UpdateError):
        upd.fetch_latest_version()


def test_fetch_latest_version_missing_tag(monkeypatch):
    monkeypatch.setattr("dualforge.update.requests.get", _fake_get({"name": "x"}))
    with pytest.raises(UpdateError):
        upd.fetch_latest_version()


def test_check_update_outdated(monkeypatch):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": "v0.4.0"})
    )
    assert upd.check_update("0.3.0") == ("0.4.0", True)


def test_check_update_current(monkeypatch):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": "v0.3.0"})
    )
    assert upd.check_update("0.3.0") == ("0.3.0", False)


# --- CLI handler ------------------------------------------------------------


def _run(monkeypatch, capsys, argv=None):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None)
    args = parser.parse_args(argv or [])
    rc = cmd_update._cmd_update_check(args)
    return rc, capsys.readouterr().out


def test_cmd_update_check_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": f"v{__version__}"})
    )
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "up to date" in out


def test_cmd_update_check_update_available(monkeypatch, capsys):
    head, _, tail = __version__.rpartition(".")
    newer = f"{head}.{int(tail) + 1}"
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": f"v{newer}"})
    )
    rc, out = _run(monkeypatch, capsys)
    assert rc == 2
    assert newer in out


def test_cmd_update_check_installed_newer(monkeypatch, capsys):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"tag_name": "v0.1.0"})
    )
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "up to date" in out


def test_cmd_update_check_failure(monkeypatch, capsys):
    monkeypatch.setattr("dualforge.update.requests.get", _fake_get({}, status=500))
    rc, out = _run(monkeypatch, capsys)
    assert rc == 1
    assert "update check failed" in out


def test_cmd_update_check_no_releases(monkeypatch, capsys):
    monkeypatch.setattr(
        "dualforge.update.requests.get", _fake_get({"message": "Not Found"}, status=404)
    )
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "up to date" in out


# --- cached check ------------------------------------------------------------


def _seed_cache(monkeypatch, tmp_path, payload):
    path = tmp_path / "update_check.json"
    if payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(upd, "_state_path", lambda: path)
    return path


def _fresh_payload(**overrides):
    payload = {
        "latest": "0.4.0",
        "is_outdated": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
    }
    payload.update(overrides)
    return payload


def test_cached_check_uses_fresh_cache(monkeypatch, tmp_path):
    _seed_cache(monkeypatch, tmp_path, _fresh_payload())
    monkeypatch.setattr(upd, "check_update", lambda *a: pytest.fail("network hit"))
    state = upd.check_update_cached("0.3.0")
    assert state.is_outdated
    assert state.latest == "0.4.0"


def test_cached_check_refreshes_stale(monkeypatch, tmp_path):
    stale = _fresh_payload(
        checked_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    )
    path = _seed_cache(monkeypatch, tmp_path, stale)
    monkeypatch.setattr(upd, "check_update", lambda current, url: ("0.5.0", True))
    state = upd.check_update_cached("0.3.0")
    assert (state.latest, state.is_outdated) == ("0.5.0", True)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["latest"] == "0.5.0"


def test_cached_check_force_ignores_fresh(monkeypatch, tmp_path):
    _seed_cache(monkeypatch, tmp_path, _fresh_payload(latest="0.4.0"))
    monkeypatch.setattr(upd, "check_update", lambda current, url: ("0.9.9", True))
    state = upd.check_update_cached("0.3.0", force=True)
    assert state.latest == "0.9.9"


def test_cached_check_error_fresh_is_not_retried(monkeypatch, tmp_path):
    error_payload = {
        "latest": None,
        "is_outdated": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": "update check failed: offline",
    }
    _seed_cache(monkeypatch, tmp_path, error_payload)
    monkeypatch.setattr(upd, "check_update", lambda *a: pytest.fail("network hit"))
    state = upd.check_update_cached("0.3.0")
    assert "offline" in state.error


def test_cached_check_error_refreshes_when_stale(monkeypatch, tmp_path):
    error_payload = {
        "latest": None,
        "is_outdated": False,
        "checked_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "error": "update check failed: offline",
    }
    _seed_cache(monkeypatch, tmp_path, error_payload)
    monkeypatch.setattr(upd, "check_update", lambda current, url: ("0.6.0", True))
    state = upd.check_update_cached("0.3.0")
    assert (state.latest, state.is_outdated) == ("0.6.0", True)


def test_cached_check_no_releases_stores_error(monkeypatch, tmp_path):
    _seed_cache(monkeypatch, tmp_path, None)

    def no_releases(current, url):
        raise NoReleasesError("no published releases")

    monkeypatch.setattr(upd, "check_update", no_releases)
    state = upd.check_update_cached("0.3.0")
    assert state.error == "no published releases"
    assert not state.is_outdated


# --- updater (perform_update) ----------------------------------------------


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_perform_update_pull_and_install(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    calls = []
    lines = []
    outcomes = [_Proc(stdout="Already up to date.\n"), _Proc(stdout="Installed")]

    def fake_run(argv, cwd, capture_output, text, timeout):
        calls.append((argv, cwd))
        return outcomes.pop(0)

    monkeypatch.setattr(upd.subprocess, "run", fake_run)
    monkeypatch.setattr(upd, "_find_source_root", lambda: root)
    monkeypatch.setattr(upd.sys, "executable", "python")

    returned = upd.perform_update(log=lines.append)
    assert returned == root
    assert calls[0][0][:2] == ["git", "-C"]
    assert calls[0][1] == root
    assert "git" in calls[0][0] and "pull" in calls[0][0]
    assert calls[1][0] == ["python", "-m", "pip", "install", ".", "--no-input"]
    assert any("Already up to date" in line for line in lines)


def test_perform_update_no_pull_skips_git(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    calls = []

    def fake_run(argv, cwd, capture_output, text, timeout):
        calls.append(argv)
        return _Proc()

    monkeypatch.setattr(upd.subprocess, "run", fake_run)
    monkeypatch.setattr(upd, "_find_source_root", lambda: root)
    monkeypatch.setattr(upd.sys, "executable", "python")
    upd.perform_update(pull=False)
    assert len(calls) == 1
    assert "pip" in calls[0]


def test_perform_update_pip_failure_raises(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def fake_run(argv, cwd, capture_output, text, timeout):
        return _Proc(returncode=1, stderr="build failed")

    monkeypatch.setattr(upd.subprocess, "run", fake_run)
    monkeypatch.setattr(upd, "_find_source_root", lambda: root)
    monkeypatch.setattr(upd.sys, "executable", "python")
    with pytest.raises(UpdateError, match="build failed"):
        upd.perform_update()


def test_perform_update_frozen_raises(monkeypatch):
    monkeypatch.setattr(upd.sys, "frozen", True, raising=False)
    with pytest.raises(UpdateError, match="cannot update"):
        upd.perform_update()


def test_perform_update_requires_source_checkout(monkeypatch, tmp_path):
    def missing(*_args, **_kwargs):
        raise UpdateError("no source checkout")

    monkeypatch.setattr(upd, "_find_source_root", missing)
    with pytest.raises(UpdateError, match="no source checkout"):
        upd.perform_update()


# --- CLI install handler -----------------------------------------------------


def _run_install(monkeypatch, capsys):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-pull", action="store_true")
    rc = cmd_update._cmd_update_install(parser.parse_args([]))
    return rc, capsys.readouterr().out


def test_cmd_update_install_ok(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    monkeypatch.setattr(upd, "check_update_cached", lambda current: upd.UpdateState(latest="0.4.0"))
    monkeypatch.setattr(upd, "perform_update", lambda **kw: root)
    rc, out = _run_install(monkeypatch, capsys)
    assert rc == 0
    assert "0.4.0" in out


def test_cmd_update_install_failure(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise UpdateError("failed to start: git")

    monkeypatch.setattr(upd, "check_update_cached", lambda current: upd.UpdateState())
    monkeypatch.setattr(upd, "perform_update", boom)
    rc, out = _run_install(monkeypatch, capsys)
    assert rc == 1
    assert "git" in out