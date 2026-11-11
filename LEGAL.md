# Publisher, license, and responsible use

## Publisher and authorship

TeamRunner is published and maintained by Serenity Partners LLC, which also
operates the TeamRunner-sponsored demo gateway at demollm.teamrunner.ai.
Kumar Muthuraman is the author and retains copyright in his work. Publishing
through the company does not transfer that copyright. Other contributors and
third-party components retain their respective rights.
Contact: hello@teamrunner.ai. Security reports: security@teamrunner.ai.

## Licenses and warranty

The TeamRunner engine and its documentation are licensed under GPL-3.0-or-later
(see LICENSE).
Shipped examples, generated starter files, and the authoring guide are
licensed under MIT-0 as identified in their license files. Third-party
components retain their own licenses and notices; see NOTICE.

THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY, TO THE EXTENT PERMITTED BY
APPLICABLE LAW. See LICENSE sections 15–17 and the applicable MIT-0 licenses
for the warranty disclaimers and limitations of liability. No accuracy,
security, availability, fitness for a particular purpose, or uninterrupted
operation is promised.

Using TeamRunner does not by itself apply the GPL to your teams, inputs, run
records or results. You may choose terms for material you own, subject to
third-party rights and applicable provider terms. No ownership, copyrightability,
exclusivity or freedom from third-party claims is promised for AI outputs.
Software licenses do not grant trademark rights or imply endorsement.

## Estimated costs and overruns

All displayed, planned, recorded, and exported monetary costs are estimates,
including amounts labeled as totals, known costs, or maximum costs. They are
not bills, quotes, or guarantees of provider charges. Rates, discounts, token
accounting, reasoning tokens, retries, tool calls, concurrent or in-flight
requests, missing usage information, configuration errors, and software or
provider failures can cause actual charges to exceed estimates or configured
budgets. A stopped or failed run can still incur charges.

TeamRunner's budget checks use its available pricing and usage information;
they do not impose a billing cap on an external provider account. Monitor
provider billing and configure provider-side spending controls where available.
You are responsible for charges incurred through your accounts and credentials.
The sponsored demo currently requires no user key and does not bill users for
its sponsored model requests; calls using your own providers remain your
responsibility.

TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, SERENITY PARTNERS LLC,
KUMAR MUTHURAMAN, AND OTHER AUTHORS, COPYRIGHT HOLDERS, AND CONTRIBUTORS
DISCLAIM LIABILITY FOR API OR TOOL CHARGES, COST OVERRUNS, LOST DATA, LOST
PROFITS, OR OTHER LOSS OR DAMAGE ARISING FROM USE OF THE SOFTWARE OR DEMO,
INCLUDING INACCURATE ESTIMATES OR FAILURE OF BUDGET CHECKS. This notice does
not exclude liability that cannot lawfully be excluded or override obligations
expressly accepted in a separate written agreement. It supplements the
applicable license disclaimers and does not restrict rights granted by those
licenses.

## Outputs and tools

AI outputs may be inaccurate, incomplete, or misleading. Independently verify
outputs and research conclusions before relying on them. TeamRunner does not
provide professional advice and is not intended for safety-critical decisions.
Review unfamiliar team files, prompts, toolkeepers, and permissions before
running them. Generated code and tools can affect the resources permitted by
their configuration. Safeguards and sandboxes are not guarantees of security.

## Data destinations and local records

Requests to hosted models transmit the selected prompts, conversation context,
and supplied content to the configured API endpoint using your configured
models and credentials. Provider terms and data practices apply. A local model
can process model requests locally, but configured tools and downloads can
still contact external services.

The sponsored demo sends prompts and conversation context through the gateway
operated by Serenity Partners LLC, then OpenRouter and its upstream model
provider. The gateway processes requests and responses and stores IP-based
quota counters to enforce usage limits. The supplied gateway configuration
does not log prompt/response bodies or API keys; this is not a promise about
upstream provider logging, retention, or training practices. Do not submit
private, confidential, sensitive, or regulated information to the demo.

The local client does not send separate usage telemetry to the publisher.
That does not mean it makes no network requests: model APIs, the demo gateway,
web tools, provider model-list checks, runtime/package downloads, diagram
assets, and configured integrations can contact their respective services.

Local run folders can retain prompts, supplied files, outputs, retrieved
content, and other sensitive information. Review and redact records before
sharing them, and avoid placing secrets in prompts or team files.

## Demo availability

The sponsored demo is an experimental, limited service. Models, quotas, and
availability may change, and service may be suspended or discontinued. No
service-level commitment is provided.
