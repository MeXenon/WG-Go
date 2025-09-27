"""Peer limit settings and session tracking utilities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Iterable, List, Optional

DEFAULT_MAX_CONCURRENT: Optional[int] = None
DEFAULT_POLICY = "new_wins"
DEFAULT_TTL_SECONDS = 180
DEFAULT_GRACE_SECONDS = 5


class PeerLimitPolicy(str, Enum):
    """Available per-peer limit eviction policies."""

    NEW_WINS = "new_wins"
    OLD_WINS = "old_wins"

    @classmethod
    def from_string(cls, value: Optional[str]) -> "PeerLimitPolicy":
        if value is None:
            return cls.NEW_WINS
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported peer limit policy: {value}") from exc


@dataclass
class PeerLimitSettings:
    max_concurrent: Optional[int] = DEFAULT_MAX_CONCURRENT
    policy: PeerLimitPolicy = PeerLimitPolicy.NEW_WINS
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    grace_seconds: int = DEFAULT_GRACE_SECONDS

    @classmethod
    def from_row(cls, row: Dict[str, object] | object) -> "PeerLimitSettings":
        mapping: Dict[str, object]
        if isinstance(row, dict):
            mapping = row
        else:
            mapping = dict(row)
        max_concurrent = mapping.get("max_concurrent")
        if isinstance(max_concurrent, (str, bytes)):
            max_concurrent = int(max_concurrent)
        if isinstance(max_concurrent, int) and max_concurrent <= 0:
            max_concurrent = None
        policy = PeerLimitPolicy.from_string(mapping.get("connection_policy"))
        ttl = mapping.get("session_ttl")
        ttl_seconds = int(ttl) if isinstance(ttl, (int, float)) else DEFAULT_TTL_SECONDS
        grace = mapping.get("grace_seconds")
        grace_seconds = int(grace) if isinstance(grace, (int, float)) else DEFAULT_GRACE_SECONDS
        return cls(
            max_concurrent=max_concurrent,
            policy=policy,
            ttl_seconds=max(ttl_seconds, 1),
            grace_seconds=max(grace_seconds, 0),
        )

    def to_record(self) -> Dict[str, object]:
        return {
            "max_concurrent": self.max_concurrent if self.max_concurrent not in (None, 0) else None,
            "connection_policy": self.policy.value,
            "session_ttl": int(self.ttl_seconds),
            "grace_seconds": int(self.grace_seconds),
        }


@dataclass
class PeerSession:
    endpoint: str
    first_seen: datetime
    last_seen: datetime
    last_handshake: Optional[datetime]
    rx_bytes: int
    tx_bytes: int
    rx_delta: int
    tx_delta: int

    @property
    def handshake_age(self) -> Optional[int]:
        if self.last_handshake is None:
            return None
        now = datetime.now(timezone.utc)
        return int((now - self.last_handshake).total_seconds())


class SessionTracker:
    """Track session endpoints for peers using TTL and grace logic."""

    def __init__(self) -> None:
        self._sessions: Dict[tuple[str, str], Dict[str, PeerSession]] = {}

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _expire(self, key: tuple[str, str], ttl_seconds: int, now: Optional[datetime] = None) -> None:
        sessions = self._sessions.get(key)
        if not sessions:
            return
        now = now or self._utcnow()
        expiry = now - timedelta(seconds=max(ttl_seconds, 1))
        stale = [endpoint for endpoint, session in sessions.items() if session.last_seen < expiry]
        for endpoint in stale:
            del sessions[endpoint]
        if not sessions:
            self._sessions.pop(key, None)

    def observe(
        self,
        interface: str,
        peer_id: str,
        endpoint: Optional[str],
        latest_handshake: Optional[int],
        rx_bytes: int,
        tx_bytes: int,
        settings: PeerLimitSettings,
        now: Optional[datetime] = None,
    ) -> List[PeerSession]:
        key = (interface, peer_id)
        now = now or self._utcnow()
        self._expire(key, settings.ttl_seconds, now)
        endpoint = endpoint or ""
        endpoint = endpoint.strip()
        sessions = self._sessions.setdefault(key, {})
        if endpoint and endpoint.lower() != "(none)":
            existing = sessions.get(endpoint)
            handshake_dt = (
                datetime.fromtimestamp(latest_handshake, tz=timezone.utc)
                if latest_handshake
                else None
            )
            rx_bytes = int(rx_bytes)
            tx_bytes = int(tx_bytes)
            if existing:
                rx_delta = max(0, rx_bytes - existing.rx_bytes)
                tx_delta = max(0, tx_bytes - existing.tx_bytes)
                if rx_delta or tx_delta:
                    last_seen = now
                else:
                    last_seen = existing.last_seen
                existing.rx_bytes = rx_bytes
                existing.tx_bytes = tx_bytes
                existing.rx_delta = rx_delta
                existing.tx_delta = tx_delta
                existing.last_seen = last_seen
                if handshake_dt and (existing.last_handshake is None or handshake_dt > existing.last_handshake):
                    existing.last_handshake = handshake_dt
            else:
                sessions[endpoint] = PeerSession(
                    endpoint=endpoint,
                    first_seen=now,
                    last_seen=now,
                    last_handshake=handshake_dt,
                    rx_bytes=rx_bytes,
                    tx_bytes=tx_bytes,
                    rx_delta=0,
                    tx_delta=0,
                )
        return list(sessions.values())

    def active_sessions(
        self,
        interface: str,
        peer_id: str,
        settings: PeerLimitSettings,
        now: Optional[datetime] = None,
    ) -> List[PeerSession]:
        now = now or self._utcnow()
        ttl_window = now - timedelta(seconds=max(settings.ttl_seconds, 1))
        sessions = self._sessions.get((interface, peer_id), {})
        active = [s for s in sessions.values() if s.last_seen >= ttl_window]
        active.sort(key=lambda s: s.last_seen, reverse=True)
        return active

    def allowed_sessions(
        self,
        interface: str,
        peer_id: str,
        settings: PeerLimitSettings,
        now: Optional[datetime] = None,
    ) -> List[PeerSession]:
        now = now or self._utcnow()
        active = self.active_sessions(interface, peer_id, settings, now)
        if settings.max_concurrent in (None, 0):
            return active
        grace_window = now - timedelta(seconds=max(settings.grace_seconds, 0))
        grace_sessions = [s for s in active if s.first_seen >= grace_window]
        stable_sessions = [s for s in active if s not in grace_sessions]
        allowed: List[PeerSession] = []
        seen: set[str] = set()

        def append_unique(items: Iterable[PeerSession]) -> None:
            for session in items:
                if session.endpoint not in seen:
                    seen.add(session.endpoint)
                    allowed.append(session)

        append_unique(grace_sessions)
        stable_allowed = [session for session in allowed if session not in grace_sessions]
        remaining = max(settings.max_concurrent - len(stable_allowed), 0)
        if remaining <= 0:
            return allowed
        if settings.policy is PeerLimitPolicy.NEW_WINS:
            ordered = stable_sessions
        else:
            ordered = sorted(stable_sessions, key=lambda s: s.first_seen)
        append_unique(ordered[:remaining])
        return allowed

    def prune_peer(self, interface: str, peer_id: str) -> None:
        self._sessions.pop((interface, peer_id), None)
