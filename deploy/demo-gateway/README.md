# Deploy demoLLM on AWS Lightsail

Public address: `https://demollm.teamrunner.ai/v1` (DNS is case-insensitive).
The same server answers TeamRunner's daily version check at
`https://version.teamrunner.ai/version.json` (D148, D149; see "The version file" below).
Operator: Serenity Partners LLC. Author: Kumar Muthuraman.
Public data and responsible-use notices: [LEGAL.md](../../LEGAL.md).

Public model: `teamrunner/demoLLM`. Upstream: `qwen/qwen3.7-flash` on OpenRouter.

This directory is a complete single-server deployment. No model weights or GPU
are needed. Python handles the gateway; Caddy handles HTTPS. The OpenRouter key
stays on the server. Deployment has not happened until the checks below pass.

## 1. Create the server

In [Lightsail](https://lightsail.aws.amazon.com/), create a Linux **OS only**
Ubuntu 24.04 LTS instance named `teamrunner-demollm`. The 1 GB plan is a sensible
starting point for this gateway. Attach a **static IPv4 address**.

In Networking, allow TCP 80 and 443 publicly. Restrict TCP 22 to your own IP
(and optionally Lightsail browser SSH). Do not open 8080. If IPv6 is enabled,
apply the same firewall rules there. No load balancer, database service or NAT
gateway is required.

Create a DNS record wherever `teamrunner.ai` is managed:

| Type | Name | Value |
| --- | --- | --- |
| A | demollm | The Lightsail static IPv4 address |
| A | version | The same Lightsail static IPv4 address |

`version` has its own record so the version check can move to another server
later by changing DNS alone, without touching demollm or any installed copy.
Use DNS-only mode if your DNS host offers an HTTP proxy. This deployment counts
the IP connecting to Caddy; placing a CDN in front requires a separate trusted
proxy configuration. Do not add an AAAA record unless IPv6 routing is configured.

References: [AWS firewall instructions](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-editing-firewall-rules.html),
[Lightsail prices](https://aws.amazon.com/lightsail/pricing/).

## 2. Install Docker and copy these files

Connect as `ubuntu` through Lightsail's browser SSH or your SSH key.
Install Docker Engine and its Compose plugin using the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository).
Check:

```sh
sudo docker version
sudo docker compose version
mkdir -p ~/teamrunner-demo-gateway
```

Copy the contents of this directory from your computer to
`/home/ubuntu/teamrunner-demo-gateway/`. For example, from this directory locally
(replace the key path and IP):

```sh
scp -i /absolute/path/to/lightsail-key.pem Dockerfile .dockerignore gateway.py compose.yaml Caddyfile gateway.env.example bootstrap-ubuntu.sh set-openrouter-key.py version.json version-stats.py ubuntu@SERVER_IP:~/teamrunner-demo-gateway/
```

Then, on the server, make the folder the version-check log goes into, owned by the
gateway's user (otherwise Docker creates it owned by root and nothing is logged):

```sh
cd ~/teamrunner-demo-gateway
mkdir -p version-logs && sudo chown 10001:10001 version-logs
```

The image build copies only `gateway.py`, never the environment file.

## 3. Add the OpenRouter key privately

Create a dedicated key in [OpenRouter](https://openrouter.ai/settings/keys).
Give it a small credit limit, such as $5, as an independent backstop. Check its
reset setting: a lifetime limit stays spent until you raise/reset it. This is
separate from gateway request allowances, which reset on UTC calendar boundaries.
Allow enough account credit for paid Qwen inference.

On the server:

```sh
cd ~/teamrunner-demo-gateway
cp gateway.env.example gateway.env
chmod 600 gateway.env
nano gateway.env
```

Replace `replace-on-server` with the key. Do not put the key in chat, a command
argument, Git, team files or website source. The defaults are:

Alternatively run `python3 set-openrouter-key.py` to enter the key at a hidden
prompt. This also sets `DEMO_ENABLED=true`; recreate the gateway afterward as
printed by the helper. For staging before a key is available, leave the placeholder
and set `DEMO_ENABLED=false`. An enabled gateway rejects the placeholder at startup.

| Setting | Default |
| --- | --- |
| Model | `qwen/qwen3.7-flash` |
| Requests per IP per minute | 30 |
| Requests per IP per UTC day | 100 |
| Total requests per UTC day | 1,000 |
| Total requests per UTC month | 10,000 |
| Maximum generated tokens per call | 512 |
| Maximum serialized conversation bytes | 16,000 |
| Concurrent provider calls | 4 |

A debate normally uses three requests; format retries and failed provider calls
also use allowance. Changing limits requires no client release.

The gateway retries brief OpenRouter rate limits internally (up to three total
attempts). Every attempt counts toward the persistent allowances. It respects
short `Retry-After` delays; a longer requested delay produces a busy response
rather than holding the connection indefinitely. If upstream capacity remains unavailable,
the client receives a clear "demo model is temporarily busy" response. Gateway
retries cannot guarantee availability in OpenRouter's shared provider pool.

## 4. Start and verify

On the server:

```sh
sudo docker compose --env-file gateway.env up -d --build
sudo docker compose --env-file gateway.env ps
curl --fail http://127.0.0.1:8080/healthz
```

When DNS points to this server, Caddy automatically obtains an HTTPS certificate.
From your computer:

```sh
curl --fail https://demollm.teamrunner.ai/v1/models
curl --fail https://version.teamrunner.ai/version.json
curl --fail https://demollm.teamrunner.ai/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"demoLLM","messages":[{"role":"user","content":"Say hello in one sentence."}],"max_tokens":80}'
```

The second command makes one paid provider request using the server's key.
Also test the real three-agent demo below: the judge uses a strict JSON schema,
so a plain greeting alone does not verify model/provider compatibility.

For connection/certificate problems:

```sh
sudo docker compose --env-file gateway.env logs --tail=50 caddy
sudo docker compose --env-file gateway.env logs --tail=50 gateway
```

The gateway logs upstream HTTP status codes, not their bodies, prompts or keys.
The quota database stores IP-based usage counters. Expired quota windows are
deleted on subsequent quota checks, so this is not a fixed deletion deadline.
Do not describe the demo as collecting no usage information. Upstream providers
have their own data practices. Caddy access logging is off. The version check
writes one line per check to `version-logs/version-checks.jsonl` (see "The version file"). `/healthz` reports process health, not paid upstream
availability. Provider failures return a generic message to clients.

## 5. Run TeamRunner

The client changes must be installed; an older PyPI build will not recognize the
new model. From the repository root on your computer:

```sh
uv tool install --force .
teamrunner new first_debate --from demo
teamrunner run first_debate --message "A four-day workweek improves productivity"
```

Users need no API key. The new example explicitly says prompts leave their
machine and are processed by TeamRunner, OpenRouter and the upstream provider.
Users can also put `default_model: teamrunner/demoLLM` on another small team
without toolkeepers. Existing examples retain their existing models.

For a staging deployment, override the gateway address in your shell or
`~/.teamrunner/.env` using `TEAMRUNNER_DEMO_API_BASE=https://YOUR_HOST/v1`.
HTTP is permitted only for localhost development. The production address is
built into the client; no address setup is required after DNS and deployment.

## Change model, limits, or pause service

Edit `gateway.env` on the server, then recreate services:

```sh
sudo docker compose --env-file gateway.env up -d --force-recreate
```

Set `DEMO_MODEL` to a different OpenRouter model ID. Test the debate after
switching: the model must accept JSON output and non-thinking mode. The default
`DEMO_STRUCTURED_OUTPUT=json_object` sends the requested schema as instructions;
TeamRunner validates the resulting fields and retries invalid replies. Qwen3.7
Flash's OpenRouter endpoint did not accept strict JSON Schema in live testing.
For a provider/model that supports strict schemas, set
`DEMO_STRUCTURED_OUTPUT=json_schema` on the server.
Generation settings and the public model alias are allowlisted; callers cannot
choose a costly model, turn on thinking, use provider plugins, request tools or
increase the output ceiling. Every completed turn records the upstream model
returned by OpenRouter and $0 as the user's cost (inference is sponsored).

Set `DEMO_ENABLED=false` to pause without changing clients. To shut it down:

```sh
sudo docker compose --env-file gateway.env stop
```

Quotas live in a persistent SQLite Docker volume. Keep the volume when updating;
do not use `down -v`, which deletes allowances and TLS state. This is a
single-instance design: do not run independent replicas with separate quota
databases. IP quotas are approximate user limits: shared networks share an
allowance, IPv6 addresses are grouped by /64, and VPNs can change IP. Global
allowances and the dedicated OpenRouter credit cap bound the total exposure.
Request allowances are not dollar budgets; changing the model changes the cost.

## The version file

`version.json` in this folder is the file every installed TeamRunner reads once a
day (D148, D149). It is in Git, and the gateway serves it read-only from disk:

```json
{"min_version": "0.0.0", "min_secure_version": "0.0.0", "message": ""}
```

- `min_version`: below it, TeamRunner prints a nudge to update and runs anyway.
  `TEAMRUNNER_NO_VERSION_CHECK=1` hides the nudge.
- `min_secure_version`: the oldest version still considered secure. Below it,
  TeamRunner refuses to run. For security problems only; nothing turns it off,
  and a copy that has seen it keeps refusing offline. It can take up to a day to
  reach everyone.
- `message`: one plain sentence saying why, shown with the nudge or refusal.
  TeamRunner writes the update command itself and never runs anything from this file.

To change a floor: edit `version.json` here, commit, then copy it to the server:

```sh
scp -i /absolute/path/to/lightsail-key.pem version.json ubuntu@SERVER_IP:~/teamrunner-demo-gateway/
curl https://version.teamrunner.ai/version.json
```

The file is read on every request, so no restart is needed. Before raising a floor,
check with `version-stats.py` which versions are still in use. A release with a
security hole is also yanked on PyPI (D148 (12)).

**What is logged.** Each check sends two headers,
`User-Agent: TeamRunner/<version> (python <major.minor>; <platform>)` and
`X-TeamRunner-Install: <random id>`. The gateway appends one JSON line with the UTC
time, TeamRunner version, Python version, platform and install id. A request whose
headers do not match that form is served but not logged. Caddy does not pass the
caller's IP to the gateway for this host, and the IP is never logged. Copies check
at most once a day, so the log is about one line per install per day.

**Reading the numbers**, on the server:

```sh
cd ~/teamrunner-demo-gateway
python3 version-stats.py          # per day: requests, installs, by version and platform
python3 version-stats.py --csv    # the same as rows for a spreadsheet
```

"Installs" per day is daily active installs; the last line is all-time distinct
installs. Keep `version-logs/` when updating, as with the quota volume.

**The demo's own floor.** `MIN_VERSION_FOR_DEMO` in `gateway.env` is the oldest
TeamRunner the demo model answers (TeamRunner sends its version with each demo
request). `0` answers every version. Set it, for example to the current
`min_secure_version`, and recreate the gateway to stop the demo answering older
copies; this is the one floor that cannot be edited away on someone's machine
(D148 (11)).

## Before announcing availability

Verify a fresh installed client can complete the debate through public HTTPS;
the judge returns a valid winner; the response records the actual model; IP and
global limits return 429; and the pause switch returns 503. Test server restart
without losing quotas. Once this works, release an updated TeamRunner package
and point the website's first-run instructions to `--from demo`.

## Publisher, license and service notices

The gateway is part of TeamRunner and uses GPL-3.0-or-later. Its author and
copyright holder is Kumar Muthuraman; Serenity Partners LLC operates the
sponsored service. See [LICENSE](../../LICENSE), [NOTICE](../../NOTICE) and
[LEGAL.md](../../LEGAL.md) for license, liability, cost and data disclosures.
Local deployments use their operator's provider accounts and data practices;
the sponsored service's description does not describe every deployment.
