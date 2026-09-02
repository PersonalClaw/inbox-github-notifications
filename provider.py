"""The inbox-github-notifications inbox provider.

Surfaces your GitHub **notifications** (review requests, mentions, CI failures,
issue activity) as PersonalClaw inbox items, via ``MessageSourceProvider`` from
``personalclaw.sdk.inbox``.

Design notes worth copying into your own source:

- **Read-only source.** GitHub notifications are not a conversation surface, so
  ``send_reply`` / ``add_reaction`` honestly return ``False`` instead of
  pretending. A source that can only read should say so.
- **Checkpoint = high-water mark.** ``poll`` keys one checkpoint under
  ``"github"`` (notifications are account-global) holding the newest
  ``updated_at`` seen; the next poll passes it as ``?since=`` so GitHub does
  the filtering.
- **Degrade to empty.** No token, network down, API error — ``poll`` logs a
  warning and returns no rows with checkpoints unchanged. An inbox source must
  never take the inbox service down with it.
- **Stdlib only.** One ``urllib`` call; nothing to install.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from personalclaw.sdk.inbox import IncomingMessage, MessageSourceProvider

logger = logging.getLogger("inbox_github_notifications")

_CHECKPOINT_KEY = "github"
DEFAULT_API_BASE = "https://api.github.com"


def _iso_to_epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InboxGithubNotificationsProvider(MessageSourceProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._timeout = int(self._config.get("timeout_secs", 20))
        self._token = str(self._config.get("token", "") or "").strip()
        self._api_base = str(
            self._config.get("api_base", DEFAULT_API_BASE) or DEFAULT_API_BASE
        ).rstrip("/")

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """The key this provider registers under."""
        return "inbox-github-notifications"

    @property
    def display_name(self) -> str:
        return "GitHub Notifications"

    @property
    def source_name(self) -> str:
        return "inbox-github-notifications"

    # ── Fetch ─────────────────────────────────────────────────────────────

    def _fetch_notifications(self, since: str) -> list[dict[str, Any]]:
        """One GET /notifications. Raises on transport/API errors — poll() is
        the degrade boundary, not this helper."""
        params = {"all": "false", "per_page": "50"}
        if since:
            params["since"] = since
        url = f"{self._api_base}/notifications?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "personalclaw-inbox-github-notifications",
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _to_row(thread: dict[str, Any]) -> IncomingMessage:
        repo = thread.get("repository") or {}
        subject = thread.get("subject") or {}
        repo_name = str(repo.get("full_name", "") or "unknown/unknown")
        reason = str(thread.get("reason", "") or "subscribed")
        subject_type = str(subject.get("type", "") or "Thread")
        title = str(subject.get("title", "") or "(no title)")
        updated_at = str(thread.get("updated_at", "") or "")
        # The subject URL tail (issue/PR number) is the closest thing a
        # notification thread has to a thread id.
        subject_url = str(subject.get("url", "") or "")
        thread_tail = subject_url.rsplit("/", 1)[-1] if subject_url else None
        return IncomingMessage(
            id=str(thread.get("id", "") or ""),
            channel_id=repo_name,
            channel_name=repo_name,
            thread_id=thread_tail,
            text=f"[{reason}] {subject_type}: {title}",
            sender_id=repo_name,
            sender_name=repo_name,
            timestamp=_iso_to_epoch(updated_at),
            is_dm=False,
            kind="mention" if reason == "mention" else "message",
        )

    # ── Contract ──────────────────────────────────────────────────────────

    async def poll(
        self, watched_channels: list[str], checkpoints: dict[str, str], user_id: str
    ) -> tuple[list[IncomingMessage], dict[str, str]]:
        """Fetch notifications newer than the checkpoint; never raise.

        ``watched_channels``, when non-empty, is a repo allow-list
        (``owner/name``) — anything else is dropped client-side.
        """
        if not self._token:
            logger.warning("github notifications: no token configured — returning empty")
            return [], dict(checkpoints)
        since = str(checkpoints.get(_CHECKPOINT_KEY, "") or "")
        try:
            threads = self._fetch_notifications(since)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("github notifications: poll failed (%s) — returning empty", exc)
            return [], dict(checkpoints)

        rows: list[IncomingMessage] = []
        newest = since
        allowed = {c.strip() for c in watched_channels if c and c.strip()}
        for thread in threads:
            row = self._to_row(thread)
            updated_at = str(thread.get("updated_at", "") or "")
            if updated_at > newest:
                newest = updated_at
            if allowed and row.channel_id not in allowed:
                continue
            rows.append(row)

        new_checkpoints = dict(checkpoints)
        # First-ever successful poll with an empty result still plants a
        # high-water mark, so history never floods in on a later poll.
        new_checkpoints[_CHECKPOINT_KEY] = newest or _now_iso()
        return rows, new_checkpoints

    async def send_reply(
        self, channel_id: str, text: str, thread_ts: str | None = None
    ) -> bool:
        """Notifications are read-only; replying happens on the PR/issue itself."""
        return False

    async def add_reaction(self, channel_id: str, ts: str, emoji: str) -> bool:
        return False

    async def get_channel_history(
        self, channel_id: str, oldest: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """No replayable per-channel history behind the notifications API."""
        return []

    async def resolve_user_name(self, user_id: str) -> str:
        return user_id


def create_provider(
    config: dict[str, Any] | None = None,
) -> InboxGithubNotificationsProvider:
    """Manifest factory — core calls this with this app's saved settings."""
    return InboxGithubNotificationsProvider(config)
