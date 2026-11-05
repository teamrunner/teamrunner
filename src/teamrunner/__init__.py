"""TeamRunner — the experimentation platform for agent organizations.

Run multi-agent orchestrations and factorial experiments entirely from
plain text files: agents are Markdown, teams are folders, experiments
are declarative YAML.

Status: preview (0.0.x, a soft launch). Until 0.1.0 is announced it may
change in ways that break earlier teams and runs; see
https://github.com/teamrunner/teamrunner.
"""

import os as _os

__version__ = "0.0.2"

# LiteLLM fetches its price table from the internet on import unless told not to. TeamRunner
# uses the table that came with the installed LiteLLM, so a run's prices depend only on what
# is installed and on ~/.teamrunner/prices.yaml (D120). Set before LiteLLM is imported anywhere.
_os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
