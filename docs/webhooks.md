# Webhooks: the office finds out at once

Until now the office learned about GitHub by asking, every five minutes. A
webhook means GitHub tells it, in about a second. Same door, same machine, one
new public path and nothing else.

## The picture

```mermaid
flowchart LR
  GH[GitHub<br/>issues · issue_comment · pull_request]
  GH -->|POST, signed| F[Tailscale Funnel<br/>:8443, mount /webhook only]
  F --> D[the door<br/>127.0.0.1:8790 /webhook]
  D -->|verify · dedupe · 204| MB[(mailbox file)]
  MB --> DR[one drainer, serial]
  DR --> DP[dispatch.sh --repo]
  DP -->|PR merged| RC[receipt]
  D -.->|gate · landed · refused| BZ[Buzz #queue]
```

Read it in that order, because each arrow is a rule:

- **One port, one path.** Funnel is per port, not per path, so it gets its own
  port with exactly one mount. Port 8443 serves `/webhook` and 404s everything
  else. Port 443 stays Serve, tailnet only, the phone's door.
- **Answer first, work after.** GitHub gives you 10 seconds and then calls the
  delivery failed. So the door verifies the signature, writes the event to a
  mailbox file, returns 204, and lets a drainer do the work.
- **One drainer, serial.** `dispatch.sh` holds a single global lock and exits 0
  when it is already held. Spawning one per event would silently drop every
  event but the first, so events queue in a file and one thread runs them one at
  a time.
- **The webhook triggers, dispatch decides.** The event says "look at this repo".
  What to do about it is still `dispatch.sh`'s call, exactly as it is on the poll.
- **Buzz is the tap on the shoulder.** A raised gate, a landed PR, a refused
  lane. Throttled per kind and subject, so a redelivery storm is one message.

## The two keychain items

Neither ever lands in a file, a log, or a commit. `_meta/services/office/office`
reads both and exports them; the client itself holds nothing.

| item | what it is | missing means |
| --- | --- | --- |
| `nexus-office` / `webhook-secret` | the HMAC GitHub signs each delivery with | `/webhook` answers 503, the rest of the office is fine |
| `buzz-tbs-hook-gh-board` / `webhook-secret` | the Buzz workflow hook into `#queue` | no taps on the shoulder, said once in the log |

`scripts/register-webhooks.sh` creates the first one on its first real run and
hands the same value to GitHub. The second already exists and is shared with
`thinking-brain-school/buzz`.

## What Aria has to click

Two things, and only these two.

**1. Turn Funnel on for the tailnet.** In the admin console, Access Controls,
expand the Funnel section and select **Add Funnel to policy**. That writes
`"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`. Without
it the command below refuses.

**2. Run one command.** Serve on 443 is already up and is not touched.

```sh
/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg --https=8443 --set-path=/webhook http://127.0.0.1:8790/webhook
```

Then check it took, and warm the certificate before GitHub is the first caller:

```sh
/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel status   # AllowFunnel on :8443, nothing else
curl -sS -o /dev/null -w '%{http_code}\n' https://this-mac.tailnet.ts.net:8443/webhook
```

Certificates are provisioned lazily on the first connection, so the first call
can time out. That is what this curl is for. "Funnel on" is not proof it works.

## Registering the hooks

One hook per repo: GitHub has no user-level webhooks. The repo list is read from
the office door, so it is exactly the desks the office already polls, and nobody
maintains a list.

```sh
cd CodingVault/nexus-office
scripts/register-webhooks.sh --dry-run https://this-mac.tailnet.ts.net:8443/webhook
scripts/register-webhooks.sh           https://this-mac.tailnet.ts.net:8443/webhook
```

It prints one line per desk: `created`, `updated`, `unchanged`, or
`skipped: <why>`. Run it as often as you like; a desk that is already right says
`unchanged` and costs one read. `--repo owner/name` does a single desk.

Today's dry run: 34 desks, 28 to create, 6 skipped because no account here has
admin on them.

One thing it deliberately cannot do: **rotate the secret.** GitHub never gives a
hook's secret back, so the script compares events and the active flag and leaves
the secret alone. To rotate, delete the hooks and re-run.

## How to tell it works

Three checks, cheapest first.

1. **The door.** `curl -s 127.0.0.1:8790/api/webhook` says whether the secret is
   configured, what has arrived, and what the last delivery did.
2. **The office.** The Webhooks card in the app shows the same thing without a
   terminal.
3. **GitHub.** The repo's Settings, Webhooks, **Recent Deliveries**. A green
   check on the `ping` means the whole path works: Funnel resolved, the
   certificate is real, the signature verified, the door answered 2xx in time. A
   red one shows the request and the response, which is usually the whole answer.

`ping` gets a 204 and nothing else happens. It is a confirmation from GitHub that
the hook is configured, not an event to act on.

If deliveries are red and the door log is silent, the request never arrived: that
is Funnel or the certificate, not the office. If the door logs a rejection, the
signature did not match, which means the keychain item and the hook were created
at different times.
