# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""TeamRunner — the experimentation platform for agent organizations.

Run multi-agent orchestrations and factorial experiments entirely from
plain text files: agents are Markdown, teams are folders, experiments
are declarative YAML.

Status: early release (0.0.x). A version may change in ways that break
earlier teams and runs; each such change is listed in the changelog at
https://github.com/teamrunner/teamrunner.
"""

import os as _os

__version__ = "0.0.6"

# LiteLLM fetches its price table from the internet on import unless told not to. TeamRunner
# uses the table that came with the installed LiteLLM, so a run's prices depend only on what
# is installed and on ~/.teamrunner/prices.yaml (D120). Set before LiteLLM is imported anywhere.
_os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
