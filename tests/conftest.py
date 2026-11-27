# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import pytest


@pytest.fixture(autouse=True)
def _no_remembered_workspace(tmp_path_factory, monkeypatch):
    """Tests never read the developer's own key file or shell workspace; a test that needs one sets it."""
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path_factory.mktemp("teamrunner_home")))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)


@pytest.fixture(autouse=True)
def _no_version_lookup(monkeypatch):
    """Tests never ask version.teamrunner.ai; test_version_check.py puts the real lookup back."""
    from teamrunner import version_check
    monkeypatch.setattr(version_check, "fetch", lambda: None)


@pytest.fixture(autouse=True)
def _no_crash_reports(monkeypatch):
    """Tests never send a crash report to version.teamrunner.ai; test_crash.py puts its own in."""
    from teamrunner import crash
    monkeypatch.setattr(crash, "post", lambda found: False)
