#!/usr/bin/env python3
"""WireGuard peer connection limiter daemon."""
from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import sqlalchemy

from modules.ConnectionString import ConnectionString
from modules.PeerLimits import PeerLimitPolicy, PeerLimitSettings, SessionTracker
from modules.PeerLimiterState import PeerLimiterStateRepository

logger = logging.getLogger("wg-go-limiter")


def split_endpoint(endpoint: str) -> Optional[Tuple[str, int]]:
    endpoint = endpoint.strip()
    if not endpoint or endpoint.lower() == "(none)":
        return None
    if endpoint.startswith("["):
        host, _, port = endpoint[1:].partition("]:")
    else:
        host, _, port = endpoint.rpartition(":")
    if not host or not port:
        return None
    try:
        return host, int(port)
    except ValueError:
        return None


class WireGuardDumpCollector:
    """Parse `wg show all dump` output."""

    def collect(self) -> Dict[str, dict]:
        cmd = shutil.which("wg")
        if not cmd:
            raise RuntimeError("wg binary not found")
        result = subprocess.run([cmd, "show", "all", "dump"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "wg show all dump failed")
        interfaces: Dict[str, dict] = {}
        current_iface: Optional[str] = None
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) == 5:
                current_iface = parts[0]
                interfaces[current_iface] = {
                    "listen_port": int(parts[3]) if parts[3] else 0,
                    "peers": []
                }
            elif len(parts) >= 8 and current_iface:
                interfaces[current_iface]["peers"].append({
                    "public_key": parts[0],
                    "endpoint": parts[2],
                    "latest_handshake": int(parts[4]) if parts[4] else 0,
                    "rx_bytes": int(parts[5]) if parts[5] else 0,
                    "tx_bytes": int(parts[6]) if parts[6] else 0,
                })
        return interfaces


class PeerLimitStore:
    """Fetch per-peer limit settings from the database."""

    def __init__(self, engine: sqlalchemy.Engine) -> None:
        self.engine = engine
        self.metadata = sqlalchemy.MetaData()
        self.tables: Dict[str, sqlalchemy.Table] = {}

    def _get_table(self, interface: str) -> Optional[sqlalchemy.Table]:
        if interface in self.tables:
            return self.tables[interface]
        try:
            table = sqlalchemy.Table(interface, self.metadata, autoload_with=self.engine)
        except Exception:
            return None
        self.tables[interface] = table
        return table

    def get_peer_settings(self, interface: str, peer_id: str) -> PeerLimitSettings:
        table = self._get_table(interface)
        if table is None:
            return PeerLimitSettings()
        with self.engine.connect() as conn:
            row = conn.execute(
                sqlalchemy.select(
                    table.c.max_concurrent,
                    table.c.connection_policy,
                    table.c.session_ttl,
                    table.c.grace_seconds,
                ).where(table.c.id == peer_id)
            ).mappings().first()
        if not row:
            return PeerLimitSettings()
        return PeerLimitSettings.from_row(row)


@dataclass
class FirewallSyncPlan:
    ipv4: set
    ipv6: set
    port: int


class FirewallBackend:
    """Abstract firewall backend."""

    def ensure_interface(self, interface: str, port: int) -> None:
        raise NotImplementedError

    def sync(self, plans: Dict[str, FirewallSyncPlan]) -> None:
        raise NotImplementedError

    def teardown_peer(self, interface: str) -> None:
        raise NotImplementedError

    @staticmethod
    def detect(logger: logging.Logger) -> Optional["FirewallBackend"]:
        if shutil.which("nft"):
            backend = NftablesBackend(logger)
            backend.ensure_environment()
            return backend
        if shutil.which("iptables") and shutil.which("ipset"):
            backend = IptablesBackend(logger)
            backend.ensure_environment()
            return backend
        logger.warning("No supported firewall backend found. Running in fail-open mode.")
        return None


class NftablesBackend(FirewallBackend):
    TABLE_NAME = "wggo_limiter"

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.initialized: set[str] = set()
        self.current_v4: Dict[str, set] = defaultdict(set)
        self.current_v6: Dict[str, set] = defaultdict(set)

    def _run(self, command: Iterable[str]) -> subprocess.CompletedProcess:
        cmd = ["nft", *command]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.logger.debug("nft command failed: %s", result.stderr.strip())
        return result

    def ensure_environment(self) -> None:
        if self._run(["list", "table", "inet", self.TABLE_NAME]).returncode != 0:
            self._run(["add", "table", "inet", self.TABLE_NAME])

    def ensure_interface(self, interface: str, port: int) -> None:
        if interface in self.initialized:
            return
        chain = f"wggo_{interface}"
        set_v4 = f"wggo_{interface}_allowed_v4"
        set_v6 = f"wggo_{interface}_allowed_v6"
        if self._run(["list", "set", "inet", self.TABLE_NAME, set_v4]).returncode != 0:
            self._run(["add", "set", "inet", self.TABLE_NAME, set_v4, "{", "type", "ipv4_addr", ".", "inet_service;", "}" ])
        if self._run(["list", "set", "inet", self.TABLE_NAME, set_v6]).returncode != 0:
            self._run(["add", "set", "inet", self.TABLE_NAME, set_v6, "{", "type", "ipv6_addr", ".", "inet_service;", "}" ])
        if self._run(["list", "chain", "inet", self.TABLE_NAME, chain]).returncode != 0:
            self._run([
                "add", "chain", "inet", self.TABLE_NAME, chain,
                "{", "type", "filter", "hook", "input", "priority", "-150;", "policy", "accept;", "}",
            ])
            # insert rules
            self._run(["add", "rule", "inet", self.TABLE_NAME, chain,
                       "udp", "dport", str(port), "ip", "saddr", ".", "udp", "sport", "@" + set_v4, "return"])
            self._run(["add", "rule", "inet", self.TABLE_NAME, chain,
                       "udp", "dport", str(port), "ip6", "saddr", ".", "udp", "sport", "@" + set_v6, "return"])
            self._run(["add", "rule", "inet", self.TABLE_NAME, chain,
                       "udp", "dport", str(port), "drop"])
        self.initialized.add(interface)

    def _format_elements(self, elements: Iterable[Tuple[str, int]]) -> str:
        parts = []
        for ip, port in elements:
            if ":" in ip and not ip.startswith("["):
                ip_repr = f"[{ip}]"
            else:
                ip_repr = ip
            parts.append(f"{ip_repr} . {port}")
        return "{ " + ", ".join(parts) + " }"

    def _sync_set(self, set_name: str, desired: set, current: set) -> set:
        to_add = desired - current
        to_remove = current - desired
        if to_add:
            self._run(["add", "element", "inet", self.TABLE_NAME, set_name, self._format_elements(to_add)])
        if to_remove:
            self._run(["delete", "element", "inet", self.TABLE_NAME, set_name, self._format_elements(to_remove)])
        return desired

    def sync(self, plans: Dict[str, FirewallSyncPlan]) -> None:
        for interface, plan in plans.items():
            self.ensure_interface(interface, plan.port)
            set_v4 = f"wggo_{interface}_allowed_v4"
            set_v6 = f"wggo_{interface}_allowed_v6"
            self.current_v4[interface] = self._sync_set(set_v4, plan.ipv4, self.current_v4.get(interface, set()))
            self.current_v6[interface] = self._sync_set(set_v6, plan.ipv6, self.current_v6.get(interface, set()))

    def teardown_peer(self, interface: str) -> None:
        self.current_v4.pop(interface, None)
        self.current_v6.pop(interface, None)


class IptablesBackend(FirewallBackend):
    """iptables/ipset fallback backend (IPv4 only)."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.current: Dict[str, set] = defaultdict(set)
        self.initialized: set[str] = set()

    def ensure_environment(self) -> None:
        # nothing to do globally
        return

    def _run(self, command: Iterable[str]) -> subprocess.CompletedProcess:
        result = subprocess.run(list(command), capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.logger.debug("command failed: %s", result.stderr.strip())
        return result

    def ensure_interface(self, interface: str, port: int) -> None:
        if interface in self.initialized:
            return
        set_name = f"wggo_{interface}_allowed"
        if self._run(["ipset", "list", set_name]).returncode != 0:
            self._run(["ipset", "create", set_name, "hash:ip,port", "family", "inet"])
        rule = ["iptables", "-C", "INPUT", "-p", "udp", "--dport", str(port), "-m", "set",
                "!", "--match-set", set_name, "src,src", "-j", "DROP"]
        if self._run(rule).returncode != 0:
            self._run(["iptables", "-I", "INPUT", "1", "-p", "udp", "--dport", str(port), "-m", "set",
                       "!", "--match-set", set_name, "src,src", "-j", "DROP"])
        self.initialized.add(interface)

    def sync(self, plans: Dict[str, FirewallSyncPlan]) -> None:
        for interface, plan in plans.items():
            if plan.ipv6:
                self.logger.warning("IPv6 endpoints not enforced with iptables backend")
            set_name = f"wggo_{interface}_allowed"
            self.ensure_interface(interface, plan.port)
            current = self.current.get(interface, set())
            to_add = plan.ipv4 - current
            to_remove = current - plan.ipv4
            for ip, port in to_add:
                self._run(["ipset", "add", set_name, f"{ip},{port}"])
            for ip, port in to_remove:
                self._run(["ipset", "del", set_name, f"{ip},{port}"])
            self.current[interface] = plan.ipv4

    def teardown_peer(self, interface: str) -> None:
        self.current.pop(interface, None)


@dataclass
class PeerLimiterDaemon:
    def __init__(self, poll_interval: float = 1.0) -> None:
        self.poll_interval = poll_interval
        self.collector = WireGuardDumpCollector()
        engine = sqlalchemy.create_engine(ConnectionString("wgdashboard"))
        self.store = PeerLimitStore(engine)
        self.tracker = SessionTracker()
        self.state_repo = PeerLimiterStateRepository(engine)
        self.backend = FirewallBackend.detect(logger)
        self._running = True
        self.metrics = {
            "last_iteration": None,
            "rules_updated": 0,
            "peers_over_limit": 0,
        }

    def stop(self, *_: object) -> None:
        self._running = False

    def run(self) -> None:
        logger.info("Starting WireGuard limiter daemon")
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while self._running:
            start = time.monotonic()
            try:
                self.iteration()
            except Exception as exc:
                logger.exception("Iteration failed: %s", exc)
            elapsed = time.monotonic() - start
            self.metrics["last_iteration"] = elapsed
            sleep_time = max(self.poll_interval - elapsed, 0.1)
            time.sleep(sleep_time)
        logger.info("Limiter daemon stopped")

    def iteration(self) -> None:
        dump = self.collector.collect()
        plans: Dict[str, FirewallSyncPlan] = {}
        over_limit_count = 0
        now = datetime.now(timezone.utc)
        for interface, info in dump.items():
            plan = FirewallSyncPlan(ipv4=set(), ipv6=set(), port=info.get("listen_port", 0))
            for peer_info in info.get("peers", []):
                peer_id = peer_info["public_key"]
                settings = self.store.get_peer_settings(interface, peer_id)
                sessions = self.tracker.observe(
                    interface,
                    peer_id,
                    peer_info.get("endpoint"),
                    peer_info.get("latest_handshake"),
                    peer_info.get("rx_bytes", 0),
                    peer_info.get("tx_bytes", 0),
                    settings,
                    now,
                )
                active = self.tracker.active_sessions(interface, peer_id, settings, now)
                allowed = self.tracker.allowed_sessions(interface, peer_id, settings, now)
                allowed_endpoints = {s.endpoint for s in allowed}
                if settings.max_concurrent not in (None, 0) and len(active) > settings.max_concurrent:
                    over_limit_count += 1
                records = []
                for session in active:
                    endpoint_tuple = split_endpoint(session.endpoint)
                    if endpoint_tuple:
                        ip, port = endpoint_tuple
                        if ":" in ip:
                            plan.ipv6.add((ip, port))
                        else:
                            plan.ipv4.add((ip, port))
                    records.append({
                        "Endpoint": session.endpoint,
                        "LastHandshake": session.last_handshake,
                        "FirstSeen": session.first_seen,
                        "LastSeen": session.last_seen,
                        "RxBytes": session.rx_bytes,
                        "TxBytes": session.tx_bytes,
                        "RxDelta": session.rx_delta,
                        "TxDelta": session.tx_delta,
                        "IsAllowed": session.endpoint in allowed_endpoints,
                    })
                self.state_repo.upsert_sessions(interface, peer_id, records)
            plans[interface] = plan
        self.metrics["peers_over_limit"] = over_limit_count
        if self.backend:
            self.backend.sync(plans)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(asctime)s] [%(levelname)s] %(message)s")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WireGuard peer limiter daemon")
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args(argv)

    configure_logging(args.verbose)

    try:
        daemon = PeerLimiterDaemon(poll_interval=args.interval)
    except Exception as exc:
        logger.error("Failed to initialize limiter: %s", exc)
        return 1
    try:
        daemon.run()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
