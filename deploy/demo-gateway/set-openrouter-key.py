"""Run on the server: enter an OpenRouter key privately and enable the demo."""
from getpass import getpass
from pathlib import Path
import os
import tempfile


def main():
    path = Path(__file__).resolve().with_name("gateway.env")
    if not path.is_file():
        raise SystemExit("Create gateway.env from gateway.env.example first.")
    key = getpass("OpenRouter API key (hidden): ").strip()
    if not key.startswith("sk-or-") or any(c.isspace() for c in key):
        raise SystemExit("That does not look like an OpenRouter key. No changes made.")
    replacement = {"OPENROUTER_API_KEY": key, "DEMO_ENABLED": "true"}
    lines = []
    for line in path.read_text().splitlines():
        name = line.partition("=")[0]
        if name in replacement:
            line = name + "=" + replacement.pop(name)
        lines.append(line)
    lines.extend(name + "=" + value for name, value in replacement.items())
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".gateway-env-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(temporary, path)  # mkstemp creates a private 0600 file
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Key saved privately. Restart the gateway to apply it:")
    print("sudo docker compose --env-file gateway.env up -d --force-recreate gateway")


if __name__ == "__main__":
    main()
