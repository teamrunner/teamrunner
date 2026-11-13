# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import pytest


@pytest.fixture(autouse=True)
def _no_remembered_workspace(tmp_path_factory, monkeypatch):
    """Tests never read the developer's own key file or shell workspace; a test that needs one sets it."""
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path_factory.mktemp("teamrunner_home")))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
