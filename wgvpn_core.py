"""
wgvpn_core.py — Educational WireGuard client core for Windows.

This module is the engine. It wraps the official WireGuard for Windows
tooling (`wireguard.exe` and `wg.exe`) which must already be installed
from https://www.wireguard.com/install/.

Why wrap, not reimplement?
    Implementing the WireGuard protocol from scratch is a research-grade
    project and would be insecure if rushed. Real shipping clients
    (including Mullvad, ProtonVPN, etc. on Windows) all delegate the
    cryptographic and tunnel work to the official WireGuard service.
    The interesting engineering — and what students should focus on —
    is the orchestration: configuration, lifecycle, status, UX.

Requires: WireGuard for Windows (installs `wireguard.exe` + `wg.exe`)
Run as:   Administrator (required to install/remove tunnel services)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Locating the WireGuard binaries
# ---------------------------------------------------------------------------

# Default install locations on a 64-bit Windows system.
DEFAULT_WG_DIRS = [
    r"C:\Program Files\WireGuard",
    r"C:\Program Files (x86)\WireGuard",
]


def find_wireguard_exe() -> Path:
    """Return the path to wireguard.exe, raising if not found."""
    # Prefer PATH lookup first — respects user customization.
    on_path = shutil.which("wireguard.exe")
    if on_path:
        return Path(on_path)

    for d in DEFAULT_WG_DIRS:
        p = Path(d) / "wireguard.exe"
        if p.exists():
            return p

    raise FileNotFoundError(
        "wireguard.exe not found. Install WireGuard for Windows from "
        "https://www.wireguard.com/install/ first."
    )


def find_wg_exe() -> Path:
    """Return the path to wg.exe (the CLI status tool)."""
    on_path = shutil.which("wg.exe")
    if on_path:
        return Path(on_path)

    for d in DEFAULT_WG_DIRS:
        p = Path(d) / "wg.exe"
        if p.exists():
            return p

    raise FileNotFoundError("wg.exe not found alongside WireGuard install.")


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------

# Where we keep user-managed .conf files. Per-user, no admin needed to read.
PROFILE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "WGEduVPN" / "profiles"


@dataclass
class Profile:
    """A named WireGuard configuration on disk."""
    name: str
    path: Path

    @property
    def tunnel_name(self) -> str:
        # WireGuard service names are derived from the .conf filename.
        return self.path.stem

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")


def ensure_profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


def list_profiles() -> list[Profile]:
    ensure_profile_dir()
    return [
        Profile(name=p.stem, path=p)
        for p in sorted(PROFILE_DIR.glob("*.conf"))
    ]


def import_profile(src: Path, name: Optional[str] = None) -> Profile:
    """Copy a .conf file into our profile directory."""
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"No such config: {src}")
    if src.suffix.lower() != ".conf":
        raise ValueError("Profile files must end in .conf")

    ensure_profile_dir()
    target_name = (name or src.stem) + ".conf"
    # Tunnel names must be filesystem- and service-safe.
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", Path(target_name).stem):
        raise ValueError(
            "Profile name must be 1–32 chars of letters, digits, _ or -."
        )

    target = PROFILE_DIR / target_name
    shutil.copyfile(src, target)
    return Profile(name=target.stem, path=target)


def delete_profile(name: str) -> None:
    p = PROFILE_DIR / f"{name}.conf"
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Tunnel lifecycle (calls wireguard.exe — needs admin)
# ---------------------------------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing output, never raising on non-zero."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        # Hide the flashing console window when called from a GUI.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def connect(profile: Profile) -> None:
    """Install and start a tunnel service for the given profile.

    `wireguard.exe /installtunnelservice <path>` registers a Windows
    service for that .conf and starts it. This is the same code path
    the official GUI uses, so behaviour is identical.
    """
    wg = find_wireguard_exe()
    result = _run([str(wg), "/installtunnelservice", str(profile.path)])
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to start tunnel '{profile.name}'. "
            f"Are you running as Administrator?\n"
            f"stderr: {result.stderr.strip()}"
        )


def disconnect(profile: Profile) -> None:
    """Stop and uninstall the tunnel service."""
    wg = find_wireguard_exe()
    result = _run([str(wg), "/uninstalltunnelservice", profile.tunnel_name])
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to stop tunnel '{profile.name}'.\n"
            f"stderr: {result.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def is_connected(profile: Profile) -> bool:
    """Cheap check: does Windows have a service for this tunnel?"""
    # `sc query WireGuardTunnel$<name>` returns 0 if the service exists.
    svc = f"WireGuardTunnel${profile.tunnel_name}"
    result = _run(["sc", "query", svc])
    return result.returncode == 0 and "RUNNING" in result.stdout


def status(profile: Profile) -> dict:
    """Return a dict of human-readable status fields, or {} if down."""
    if not is_connected(profile):
        return {}

    wg = find_wg_exe()
    # `wg show <iface> dump` is the machine-readable form.
    result = _run([str(wg), "show", profile.tunnel_name, "dump"])
    if result.returncode != 0:
        return {}

    lines = [ln for ln in result.stdout.strip().splitlines() if ln]
    if len(lines) < 2:
        return {}

    # First line is interface, subsequent lines are peers. For an edu
    # client we typically have exactly one peer.
    peer = lines[1].split("\t")
    # Format: pubkey, psk, endpoint, allowed-ips, latest-handshake, rx, tx, keepalive
    if len(peer) < 8:
        return {}

    handshake_unix = int(peer[4]) if peer[4].isdigit() else 0
    return {
        "endpoint": peer[2],
        "allowed_ips": peer[3],
        "last_handshake_unix": handshake_unix,
        "rx_bytes": int(peer[5]) if peer[5].isdigit() else 0,
        "tx_bytes": int(peer[6]) if peer[6].isdigit() else 0,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

USAGE = """\
wgvpn — educational WireGuard client (Windows)

Commands:
  list                          Show known profiles
  import <path.conf> [name]     Copy a config into the profile store
  delete <name>                 Remove a profile
  up <name>                     Connect (requires Administrator)
  down <name>                   Disconnect (requires Administrator)
  status <name>                 Show connection status as JSON
  paths                         Show where things live
"""


def _profile_or_die(name: str) -> Profile:
    for p in list_profiles():
        if p.name == name:
            return p
    print(f"No such profile: {name}", file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    cmd, *rest = argv

    try:
        if cmd == "list":
            profiles = list_profiles()
            if not profiles:
                print("(no profiles — use `import` first)")
            for p in profiles:
                marker = "●" if is_connected(p) else "○"
                print(f"  {marker} {p.name}")
            return 0

        if cmd == "import":
            if not rest:
                print("Usage: import <path.conf> [name]", file=sys.stderr)
                return 2
            src = Path(rest[0])
            name = rest[1] if len(rest) > 1 else None
            p = import_profile(src, name)
            print(f"Imported: {p.name}")
            return 0

        if cmd == "delete":
            if not rest:
                print("Usage: delete <name>", file=sys.stderr)
                return 2
            delete_profile(rest[0])
            print(f"Deleted: {rest[0]}")
            return 0

        if cmd == "up":
            if not rest:
                print("Usage: up <name>", file=sys.stderr)
                return 2
            connect(_profile_or_die(rest[0]))
            print(f"Connected: {rest[0]}")
            return 0

        if cmd == "down":
            if not rest:
                print("Usage: down <name>", file=sys.stderr)
                return 2
            disconnect(_profile_or_die(rest[0]))
            print(f"Disconnected: {rest[0]}")
            return 0

        if cmd == "status":
            if not rest:
                print("Usage: status <name>", file=sys.stderr)
                return 2
            p = _profile_or_die(rest[0])
            info = status(p)
            print(json.dumps(
                {"profile": p.name, "connected": bool(info), **info},
                indent=2,
            ))
            return 0

        if cmd == "paths":
            print(f"Profiles dir: {PROFILE_DIR}")
            try:
                print(f"wireguard.exe: {find_wireguard_exe()}")
                print(f"wg.exe:        {find_wg_exe()}")
            except FileNotFoundError as e:
                print(f"WireGuard tools: NOT FOUND — {e}")
            return 0

        print(f"Unknown command: {cmd}\n\n{USAGE}", file=sys.stderr)
        return 2

    except Exception as e:  # noqa: BLE001 — friendly CLI errors
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
