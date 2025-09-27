"""Database helpers for persisting limiter session state."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

import sqlalchemy as sa


class PeerLimiterStateRepository:
    """Persist limiter state for API consumption."""

    def __init__(self, engine: sa.Engine):
        self.engine = engine
        self.metadata = sa.MetaData()
        self.sessions_table = sa.Table(
            "PeerLimiterSessions",
            self.metadata,
            sa.Column("Interface", sa.String(255), nullable=False),
            sa.Column("PeerID", sa.String(255), nullable=False),
            sa.Column("Endpoint", sa.String(255), nullable=False),
            sa.Column("LastHandshake", sa.DateTime(timezone=True)),
            sa.Column("FirstSeen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("LastSeen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("RxBytes", sa.BigInteger, nullable=False, default=0),
            sa.Column("TxBytes", sa.BigInteger, nullable=False, default=0),
            sa.Column("RxDelta", sa.BigInteger, nullable=False, default=0),
            sa.Column("TxDelta", sa.BigInteger, nullable=False, default=0),
            sa.Column("IsAllowed", sa.Boolean, nullable=False, default=True),
            sa.Column("UpdatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
            sa.PrimaryKeyConstraint("Interface", "PeerID", "Endpoint"),
            extend_existing=True,
        )
        self.metadata.create_all(self.engine)

    def upsert_sessions(
        self,
        interface: str,
        peer_id: str,
        sessions: Iterable[dict],
    ) -> None:
        records: List[dict] = []
        now = datetime.now(timezone.utc)
        for session in sessions:
            record = {
                "Interface": interface,
                "PeerID": peer_id,
                "Endpoint": session["Endpoint"],
                "LastHandshake": session.get("LastHandshake"),
                "FirstSeen": session.get("FirstSeen", now),
                "LastSeen": session.get("LastSeen", now),
                "RxBytes": int(session.get("RxBytes", 0)),
                "TxBytes": int(session.get("TxBytes", 0)),
                "RxDelta": int(session.get("RxDelta", 0)),
                "TxDelta": int(session.get("TxDelta", 0)),
                "IsAllowed": bool(session.get("IsAllowed", True)),
                "UpdatedAt": now,
            }
            records.append(record)
        with self.engine.begin() as conn:
            conn.execute(
                self.sessions_table.delete().where(
                    sa.and_(
                        self.sessions_table.c.Interface == interface,
                        self.sessions_table.c.PeerID == peer_id,
                    )
                )
            )
            if records:
                conn.execute(self.sessions_table.insert(), records)

    def purge_interface(self, interface: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                self.sessions_table.delete().where(self.sessions_table.c.Interface == interface)
            )

    def get_sessions(self, interface: str, peer_id: str) -> List[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(self.sessions_table)
                .where(self.sessions_table.c.Interface == interface)
                .where(self.sessions_table.c.PeerID == peer_id)
                .order_by(self.sessions_table.c.LastSeen.desc())
            ).mappings().all()
        result: List[dict] = []
        now = datetime.now(timezone.utc)
        for row in rows:
            last_handshake = row["LastHandshake"]
            handshake_age = None
            if last_handshake:
                handshake_age = int((now - last_handshake).total_seconds())
            result.append({
                "endpoint": row["Endpoint"],
                "lastHandshake": last_handshake.isoformat() if last_handshake else None,
                "handshakeAgeSeconds": handshake_age,
                "firstSeen": row["FirstSeen"].isoformat() if row["FirstSeen"] else None,
                "lastSeen": row["LastSeen"].isoformat() if row["LastSeen"] else None,
                "rxBytes": int(row["RxBytes"] or 0),
                "txBytes": int(row["TxBytes"] or 0),
                "rxDelta": int(row["RxDelta"] or 0),
                "txDelta": int(row["TxDelta"] or 0),
                "allowed": bool(row["IsAllowed"]),
            })
        return result

