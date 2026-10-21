"""SSH authentication for a remote harness host.

The credential comes from the request body or from a named environment
variable. It is not written to pool YAML, catalog YAML, a harness profile,
or git. Authentication may open one SSH session and run the fixed command
``true``. It does not read a package inventory and it does not install.

Remote inventory and install stay unsupported after a successful login.
``ohbe.library.remote_exec_supported`` is the switch a later executor can
flip. Denied hosts are rejected before a socket or an ``ssh`` process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,31}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ASKPASS = "#!/bin/sh\nprintf '%s\\n' \"$OHBE_SSH_AUTH_SECRET\"\n"

AuthMethod = Literal["ssh_key", "password"]


class RemoteAuthError(Exception):
    """Authentication stopped. ``reason`` is a public library reason code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RemoteCredential:
    """In-memory SSH material. Callers must not persist ``secret``."""

    username: str
    method: AuthMethod
    secret: str


def load_credential(
    *,
    username: str,
    auth_method: str,
    secret: str,
    secret_env: str,
    environ: Mapping[str, str],
) -> RemoteCredential:
    """Resolve a key or password. A set ``secret_env`` wins and is not echoed."""

    if not isinstance(username, str) or _USER.fullmatch(username) is None:
        raise RemoteAuthError("auth_required")
    if auth_method not in {"ssh_key", "password"}:
        raise RemoteAuthError("auth_required")
    if not isinstance(secret, str) or not isinstance(secret_env, str):
        raise RemoteAuthError("auth_required")
    material = ""
    if secret_env.strip():
        if _ENV_NAME.fullmatch(secret_env) is None:
            raise RemoteAuthError("auth_required")
        found = environ.get(secret_env, "")
        if not isinstance(found, str) or not found:
            raise RemoteAuthError("auth_required")
        material = found
    elif secret:
        material = secret
    else:
        raise RemoteAuthError("auth_required")
    if not _secret_ok(auth_method, material):
        raise RemoteAuthError("auth_required")
    return RemoteCredential(username=username, method=auth_method, secret=material)  # type: ignore[arg-type]


def authenticate(host: str, port: int, credential: RemoteCredential, *, timeout: float = 8.0) -> None:
    """Prove the credential against ``host``. Raise ``RemoteAuthError`` on failure.

    The secret is passed in a key file or in the askpass environment. It is
    not placed on the ``ssh`` argument list.
    """

    from ohbe.library import classify_host

    if classify_host(host) != "ok":
        raise RemoteAuthError("rejected_host")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise RemoteAuthError("rejected_host")
    ssh = shutil.which("ssh")
    if not ssh:
        raise RemoteAuthError("auth_unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="ohbe-ssh-") as directory:
            argv, env = _ssh_invocation(ssh, directory, host, port, credential, timeout)
            if any(part == credential.secret for part in argv):
                raise RemoteAuthError("auth_failed")
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(timeout, 1) + 2,
                env=env,
                start_new_session=True,
            )
    except RemoteAuthError:
        raise
    except subprocess.TimeoutExpired:
        raise RemoteAuthError("unreachable") from None
    except OSError:
        raise RemoteAuthError("auth_unavailable") from None
    if completed.returncode == 0:
        return
    stderr = (completed.stderr or "").lower()
    if "permission denied" in stderr or "authentication failed" in stderr:
        raise RemoteAuthError("auth_failed") from None
    if (
        "could not resolve" in stderr
        or "connection refused" in stderr
        or "timed out" in stderr
        or "no route to host" in stderr
        or "network is unreachable" in stderr
    ):
        raise RemoteAuthError("unreachable") from None
    raise RemoteAuthError("auth_failed") from None


def _ssh_invocation(
    ssh: str,
    directory: str,
    host: str,
    port: int,
    credential: RemoteCredential,
    timeout: float,
) -> tuple[list[str], dict[str, str]]:
    known_hosts = os.path.join(directory, "known_hosts")
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C"}
    argv = [
        ssh,
        "-F",
        "/dev/null",
        "-p",
        str(port),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        f"ConnectTimeout={max(1, int(timeout))}",
        "-o",
        "NumberOfPasswordPrompts=1",
        "-l",
        credential.username,
    ]
    if credential.method == "ssh_key":
        key_path = os.path.join(directory, "id")
        _write_secret_file(key_path, _key_text(credential.secret), 0o600)
        argv.extend(
            [
                "-i",
                key_path,
                "-o",
                "BatchMode=yes",
                "-o",
                "PreferredAuthentications=publickey",
                "-o",
                "PubkeyAuthentication=yes",
                "-o",
                "PasswordAuthentication=no",
            ]
        )
    else:
        askpass = os.path.join(directory, "askpass")
        _write_secret_file(askpass, _ASKPASS, 0o700)
        env["SSH_ASKPASS"] = askpass
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = "ohbe"
        env["OHBE_SSH_AUTH_SECRET"] = credential.secret
        argv.extend(
            [
                "-o",
                "BatchMode=no",
                "-o",
                "PreferredAuthentications=password",
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "KbdInteractiveAuthentication=no",
            ]
        )
    argv.extend([host, "true"])
    return argv, env


def _key_text(secret: str) -> str:
    if secret.endswith("\n"):
        return secret
    return secret + "\n"


def _write_secret_file(path: str, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def _secret_ok(method: str, secret: str) -> bool:
    if not isinstance(secret, str) or "\x00" in secret or len(secret) > 65536:
        return False
    if method == "password":
        if secret.strip() == "" or "\n" in secret or "\r" in secret:
            return False
        return True
    if "PRIVATE KEY" not in secret or len(secret) < 32:
        return False
    return True
