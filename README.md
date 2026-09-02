# GitHub Notifications Inbox

A PersonalClaw **inbox** app that surfaces your GitHub notifications — review
requests, mentions, CI failures, issue activity — as inbox items. Implements
`MessageSourceProvider` from `personalclaw.sdk.inbox`, stdlib-only (one
`urllib` call, nothing to install).

## Setup

1. Create a GitHub personal access token with the `notifications` scope
   (classic PAT) — fine-grained tokens need **Notifications: read**.
2. Install the app (below), then set the token in its settings.

| Key | Default | Meaning |
| --- | --- | --- |
| `token` | *(required)* | GitHub token with notifications read access |
| `api_base` | `https://api.github.com` | Override for GitHub Enterprise |
| `timeout_secs` | `20` | HTTP timeout per poll |

Watched channels (configured in the platform's inbox settings) act as a repo
allow-list — `owner/name` entries; leave empty to take everything.

## How each row maps

| Inbox field | From |
| --- | --- |
| `channel_id` / `channel_name` | repository `full_name` |
| `thread_id` | the issue/PR number off the subject URL |
| `text` | `[reason] SubjectType: title` — e.g. `[review_requested] PullRequest: Fix the flux capacitor` |
| `kind` | `mention` when GitHub's reason is `mention`, else `message` |
| `timestamp` | the thread's `updated_at` |

## Design notes worth copying into your own source

- **Read-only source.** Notifications aren't a conversation surface, so
  `send_reply` / `add_reaction` honestly return `False`. Reply on the PR or
  issue itself.
- **Checkpoint = high-water mark.** One checkpoint (key `github`) holds the
  newest `updated_at` seen; the next poll sends it as `?since=` so GitHub does
  the filtering. Filtered-out rows still advance the mark, and an empty first
  poll plants one — so history never floods in later.
- **Degrade to empty.** Missing token, network failure, API error: `poll` logs
  a warning and returns no rows with checkpoints unchanged. An inbox source
  must never take the inbox service down with it.

## Run the tests

```bash
pytest .
```

No network — the behaviour tests stub the fetch.

## Install it

From the dashboard: **Store → Add source**, point it at this repo's git URL (or
a local clone), then install and enable it.

## License

MIT
