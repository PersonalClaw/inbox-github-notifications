"""Contract + behaviour tests for the inbox-github-notifications provider.

Contract: personalclaw.sdk.inbox:MessageSourceProvider

No network: behaviour tests stub ``_fetch_notifications`` and assert the
mapping, checkpointing, filtering, and degrade-to-empty rules.
"""

from __future__ import annotations

import asyncio
import urllib.error

from provider import InboxGithubNotificationsProvider, create_provider

CONTRACT_METHODS = (
    "add_reaction",
    "get_channel_history",
    "poll",
    "resolve_user_name",
    "send_reply",
    "source_name",
)


def _thread(
    thread_id: str = "1001",
    repo: str = "PersonalClaw/PersonalClaw",
    reason: str = "review_requested",
    title: str = "Fix the flux capacitor",
    updated_at: str = "2026-09-02T12:00:00Z",
) -> dict:
    return {
        "id": thread_id,
        "reason": reason,
        "updated_at": updated_at,
        "repository": {"full_name": repo},
        "subject": {
            "title": title,
            "type": "PullRequest",
            "url": f"https://api.github.com/repos/{repo}/pulls/42",
        },
    }


# ── Contract shape ────────────────────────────────────────────────────────


def test_factory_returns_the_provider() -> None:
    assert isinstance(create_provider({}), InboxGithubNotificationsProvider)


def test_factory_accepts_no_config() -> None:
    assert isinstance(create_provider(None), InboxGithubNotificationsProvider)


def test_nothing_abstract_is_left() -> None:
    assert not getattr(
        InboxGithubNotificationsProvider, "__abstractmethods__", frozenset()
    )


def test_registers_under_the_app_name() -> None:
    provider = create_provider({})
    assert provider.name == "inbox-github-notifications"
    assert provider.source_name == "inbox-github-notifications"


def test_every_contract_method_is_declared() -> None:
    for name in CONTRACT_METHODS:
        assert name in vars(
            InboxGithubNotificationsProvider
        ), f"{name} is not implemented"


def test_settings_reach_the_provider() -> None:
    assert create_provider({"timeout_secs": 5})._timeout == 5


# ── Behaviour: poll mapping + checkpoints ─────────────────────────────────


def _poll(provider, watched=None, checkpoints=None):
    return asyncio.run(provider.poll(watched or [], checkpoints or {}, "owner"))


def test_no_token_returns_empty_and_keeps_checkpoints() -> None:
    rows, cps = _poll(create_provider({}), checkpoints={"github": "X"})
    assert rows == []
    assert cps == {"github": "X"}


def test_threads_map_to_inbox_rows() -> None:
    p = create_provider({"token": "t"})
    p._fetch_notifications = lambda since: [_thread()]
    rows, cps = _poll(p)
    (row,) = rows
    assert row.id == "1001"
    assert row.channel_id == "PersonalClaw/PersonalClaw"
    assert row.thread_id == "42"
    assert row.text == "[review_requested] PullRequest: Fix the flux capacitor"
    assert row.kind == "message"
    assert row.timestamp > 0
    assert cps["github"] == "2026-09-02T12:00:00Z"


def test_mention_reason_maps_to_mention_kind() -> None:
    p = create_provider({"token": "t"})
    p._fetch_notifications = lambda since: [_thread(reason="mention")]
    rows, _ = _poll(p)
    assert rows[0].kind == "mention"


def test_checkpoint_is_the_newest_updated_at() -> None:
    p = create_provider({"token": "t"})
    p._fetch_notifications = lambda since: [
        _thread(thread_id="1", updated_at="2026-09-02T10:00:00Z"),
        _thread(thread_id="2", updated_at="2026-09-02T14:00:00Z"),
        _thread(thread_id="3", updated_at="2026-09-02T12:00:00Z"),
    ]
    _, cps = _poll(p)
    assert cps["github"] == "2026-09-02T14:00:00Z"


def test_checkpoint_is_passed_through_as_since() -> None:
    seen = {}
    p = create_provider({"token": "t"})

    def fetch(since):
        seen["since"] = since
        return []

    p._fetch_notifications = fetch
    _poll(p, checkpoints={"github": "2026-09-01T00:00:00Z"})
    assert seen["since"] == "2026-09-01T00:00:00Z"


def test_watched_channels_filter_by_repo() -> None:
    p = create_provider({"token": "t"})
    p._fetch_notifications = lambda since: [
        _thread(thread_id="1", repo="PersonalClaw/PersonalClaw"),
        _thread(thread_id="2", repo="other/repo"),
    ]
    rows, _ = _poll(p, watched=["PersonalClaw/PersonalClaw"])
    assert [r.id for r in rows] == ["1"]


def test_filtered_threads_still_advance_the_checkpoint() -> None:
    """The high-water mark tracks what was SEEN, not what was kept — otherwise
    a filtered-out newest thread would be re-fetched forever."""
    p = create_provider({"token": "t"})
    p._fetch_notifications = lambda since: [
        _thread(thread_id="1", repo="other/repo", updated_at="2026-09-02T15:00:00Z"),
    ]
    rows, cps = _poll(p, watched=["PersonalClaw/PersonalClaw"])
    assert rows == []
    assert cps["github"] == "2026-09-02T15:00:00Z"


def test_empty_first_poll_plants_a_high_water_mark() -> None:
    p = create_provider({"token": "t"})
    p._fetch_notifications = lambda since: []
    _, cps = _poll(p)
    assert cps["github"]  # planted, so history never floods later


def test_api_failure_degrades_to_empty() -> None:
    p = create_provider({"token": "t"})

    def boom(since):
        raise urllib.error.URLError("down")

    p._fetch_notifications = boom
    rows, cps = _poll(p, checkpoints={"github": "keep-me"})
    assert rows == []
    assert cps == {"github": "keep-me"}


def test_read_only_surface_says_so() -> None:
    p = create_provider({"token": "t"})
    assert asyncio.run(p.send_reply("c", "text")) is False
    assert asyncio.run(p.add_reaction("c", "ts", "eyes")) is False
    assert asyncio.run(p.get_channel_history("c", "0")) == []
    assert asyncio.run(p.resolve_user_name("someone")) == "someone"
