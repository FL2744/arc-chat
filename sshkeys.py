"""Local SSH key generation/inspection for ARC connection setup.

Private keys never leave the machine; only the public key is returned for
registration with ARC. ARC's currently supported algorithms must be confirmed
against ARC documentation; ed25519 is the default and ``rsa`` (4096) is offered.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
SUPPORTED = {"ed25519": [], "rsa": ["-b", "4096"]}


def default_key_dir() -> Path:
    return Path.home() / ".ssh"


def validate_arc_username(username: str) -> str:
    """Reject the common mistake of entering a VT email address as the ARC username."""
    value = str(username or "").strip()
    if "@" in value:
        raise ValueError(
            f"'{value}' looks like an email address. Enter your ARC username (for example the part "
            "before '@vt.edu'), not your Virginia Tech email address."
        )
    if not USERNAME_RE.fullmatch(value):
        raise ValueError(
            f"ARC could not accept username '{value}'. Use lowercase letters, digits, '_' or '-', "
            "starting with a letter."
        )
    return value


@dataclass(frozen=True)
class KeyPair:
    private_path: Path
    public_path: Path
    public_key: str


def generate_key(name: str = "arc_research_ed25519", *, directory: Path | None = None,
                 algorithm: str = "ed25519", comment: str = "arc-research") -> KeyPair:
    """Generate a passphrase-less-by-default keypair with ssh-keygen; never overwrites."""
    if algorithm not in SUPPORTED:
        raise ValueError(f"Unsupported key algorithm '{algorithm}'. Choose one of: {', '.join(SUPPORTED)}.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        raise ValueError("Key name may only contain letters, digits, '.', '_' and '-'.")
    exe = shutil.which("ssh-keygen")
    if not exe:
        raise RuntimeError("ssh-keygen was not found. Install the OpenSSH client and retry.")
    directory = Path(directory or default_key_dir())
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    private = directory / name
    if private.exists() or private.with_suffix(private.suffix + ".pub").exists():
        raise FileExistsError(f"A key named {private} already exists; choose another name or select it.")
    subprocess.run([exe, "-q", "-t", algorithm, *SUPPORTED[algorithm], "-N", "", "-C", comment, "-f", str(private)],
                   check=True, capture_output=True, timeout=60)
    public = Path(str(private) + ".pub")
    return KeyPair(private, public, public.read_text(encoding="utf-8").strip())


def check_key_permissions(private_key: Path) -> list[str]:
    """Return human-readable problems with a private key file (POSIX only)."""
    path = Path(private_key)
    if not path.exists():
        return [f"Private key {path} does not exist."]
    problems = []
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        problems.append(f"{path} is readable by other users; run: chmod 600 {path}")
    if not Path(str(path) + ".pub").exists():
        problems.append(f"No matching public key {path}.pub found; it can be regenerated with ssh-keygen -y.")
    return problems


def list_existing_keys(directory: Path | None = None) -> list[Path]:
    directory = Path(directory or default_key_dir())
    if not directory.is_dir():
        return []
    return sorted(Path(str(p)[:-4]) for p in directory.glob("*.pub") if Path(str(p)[:-4]).is_file())


def translate_ssh_failure(stderr: str, username: str = "") -> str:
    """Turn raw SSH errors into actionable text."""
    text = stderr.lower()
    who = f" for '{username}'" if username else ""
    if "permission denied" in text:
        return (f"ARC rejected the SSH key{who}. Confirm the username is your ARC username (not your email) "
                "and that the public key has been registered with ARC.")
    if "could not resolve" in text or "timed out" in text or "no route" in text or "connection refused" in text:
        return "Could not reach ARC. Connect to the Virginia Tech network/VPN and retry."
    if "unprotected private key" in text or "bad permissions" in text:
        return "SSH refuses the private key because its file permissions are too open. Restrict it to your user (chmod 600)."
    if "host key verification failed" in text:
        return "The ARC host key did not match a known entry. Verify you are connecting to the correct host before updating known_hosts."
    return "SSH connection failed. Expand the diagnostic details for the raw error."
