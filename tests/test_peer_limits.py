from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import logging

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from modules.PeerLimits import PeerLimitPolicy, PeerLimitSettings, SessionTracker
from peer_limiter_daemon import NftablesBackend, FirewallSyncPlan


class DummyResult:
    def __init__(self):
        self.returncode = 0


def test_peer_limit_settings_parsing_defaults():
    settings = PeerLimitSettings.from_row({})
    assert settings.max_concurrent is None
    assert settings.policy is PeerLimitPolicy.NEW_WINS
    assert settings.ttl_seconds == 180
    assert settings.grace_seconds == 5

    settings = PeerLimitSettings.from_row({
        "max_concurrent": 0,
        "connection_policy": "old_wins",
        "session_ttl": 60,
        "grace_seconds": 10,
    })
    assert settings.max_concurrent is None
    assert settings.policy is PeerLimitPolicy.OLD_WINS
    assert settings.ttl_seconds == 60
    assert settings.grace_seconds == 10


def test_session_tracker_new_wins_policy():
    tracker = SessionTracker()
    settings = PeerLimitSettings(max_concurrent=1, policy=PeerLimitPolicy.NEW_WINS, grace_seconds=0)
    now = datetime.now(timezone.utc)

    first_time = now - timedelta(seconds=5)
    tracker.observe("wg0", "peer", "10.0.0.1:50000", int(first_time.timestamp()), 100, 0, settings, first_time)
    tracker.observe("wg0", "peer", "10.0.0.2:50001", int(now.timestamp()), 200, 0, settings, now)

    allowed_now = tracker.allowed_sessions("wg0", "peer", settings, now)
    assert {session.endpoint for session in allowed_now} == {"10.0.0.1:50000", "10.0.0.2:50001"}

    later = now + timedelta(seconds=1)
    allowed_later = tracker.allowed_sessions("wg0", "peer", settings, later)
    assert [session.endpoint for session in allowed_later] == ["10.0.0.2:50001"]


def test_session_tracker_old_wins_policy():
    tracker = SessionTracker()
    settings = PeerLimitSettings(max_concurrent=1, policy=PeerLimitPolicy.OLD_WINS, grace_seconds=0)
    now = datetime.now(timezone.utc)

    earlier = now - timedelta(seconds=10)
    tracker.observe("wg0", "peer", "10.0.0.1:50000", int(earlier.timestamp()), 100, 0, settings, earlier)
    tracker.observe("wg0", "peer", "10.0.0.2:50001", int(now.timestamp()), 200, 0, settings, now)

    allowed_now = tracker.allowed_sessions("wg0", "peer", settings, now)
    assert {session.endpoint for session in allowed_now} == {"10.0.0.1:50000", "10.0.0.2:50001"}

    later = now + timedelta(seconds=1)
    allowed_later = tracker.allowed_sessions("wg0", "peer", settings, later)
    assert [session.endpoint for session in allowed_later] == ["10.0.0.1:50000"]


def test_session_tracker_grace_period_allows_both():
    tracker = SessionTracker()
    settings = PeerLimitSettings(max_concurrent=1, policy=PeerLimitPolicy.NEW_WINS, grace_seconds=10)
    now = datetime.now(timezone.utc)

    first_time = now - timedelta(seconds=20)
    tracker.observe("wg0", "peer", "10.0.0.1:50000", int(first_time.timestamp()), 100, 0, settings, first_time)
    tracker.observe("wg0", "peer", "10.0.0.2:50001", int(now.timestamp()), 200, 0, settings, now)

    allowed_now = tracker.allowed_sessions("wg0", "peer", settings, now)
    assert {session.endpoint for session in allowed_now} == {"10.0.0.1:50000", "10.0.0.2:50001"}

    midway = now + timedelta(seconds=5)
    allowed_midway = tracker.allowed_sessions("wg0", "peer", settings, midway)
    assert {session.endpoint for session in allowed_midway} == {"10.0.0.1:50000", "10.0.0.2:50001"}

    after_grace = now + timedelta(seconds=11)
    allowed_later = tracker.allowed_sessions("wg0", "peer", settings, after_grace)
    assert [session.endpoint for session in allowed_later] == ["10.0.0.2:50001"]


def test_session_tracker_ttl_expires_sessions():
    tracker = SessionTracker()
    settings = PeerLimitSettings(max_concurrent=1, ttl_seconds=5)
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(seconds=10)
    tracker.observe("wg0", "peer", "10.0.0.1:50000", int(earlier.timestamp()), 100, 0, settings, earlier)
    active = tracker.active_sessions("wg0", "peer", settings, now)
    assert active == []


def test_nftables_backend_diff_operations(monkeypatch):
    backend = NftablesBackend(logging.getLogger("test"))
    backend.current_v4["wg0"] = {("10.0.0.1", 1111)}
    backend.current_v6["wg0"] = set()
    commands = []

    def fake_run(args):
        commands.append(args)
        result = DummyResult()
        return result

    backend._run = fake_run  # type: ignore
    plan = {"wg0": FirewallSyncPlan(ipv4={("10.0.0.1", 1111), ("10.0.0.2", 2222)}, ipv6=set(), port=51820)}
    backend.ensure_interface = lambda iface, port: None  # type: ignore
    backend.sync(plan)

    add_commands = [cmd for cmd in commands if "add" in cmd]
    assert any("10.0.0.2" in " ".join(cmd) for cmd in add_commands)

    commands.clear()
    backend.current_v4["wg0"] = {("10.0.0.1", 1111), ("10.0.0.2", 2222)}
    plan = {"wg0": FirewallSyncPlan(ipv4={("10.0.0.2", 2222)}, ipv6=set(), port=51820)}
    backend.sync(plan)
    delete_commands = [cmd for cmd in commands if "delete" in cmd]
    assert any("10.0.0.1" in " ".join(cmd) for cmd in delete_commands)
