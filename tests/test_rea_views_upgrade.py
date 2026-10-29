"""Exercise the operator upgrade using a temporary host and command doubles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / "deploy/rea-vault/upgrade-analysis.sh"
OLD = "# old reviewed relay\n"
NEW = "# new reviewed relay\n"

COMMAND_DOUBLE = r"""#!/usr/bin/env python3
import json, os, pathlib, socket, subprocess, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
with (root / "commands.jsonl").open("a") as out:
    out.write(json.dumps([name, args]) + "\n")
failure = os.environ.get("FAIL_AT", "")
if name == "git":
    if args[-2:] == ["branch", "--show-current"]:
        print(os.environ.get("FIXTURE_BRANCH", "main"))
    elif args[-2:] == ["status", "--porcelain"]:
        print(" M local-change" if os.environ.get("FIXTURE_DIRTY") else "")
    elif "show" in args:
        rel = args[-1].split(":", 1)[1]
        print({"host/rea_relay.py": "# old reviewed relay\n",
               "host/rea_store.py": "reviewed store\n",
               "deploy/rea-bridge/crew-rea-relay.service": "reviewed unit\n"}[rel],
              end="")
    else:
        raise SystemExit("unexpected git arguments")
elif name == "install":
    clean = []
    at = 0
    while at < len(args):
        if args[at] in ("-o", "-g"):
            at += 2
        else:
            clean.append(args[at])
            at += 1
    raise SystemExit(subprocess.run(["/usr/bin/install", *clean]).returncode)
elif name == "systemctl":
    assert args[-1] == "crew-rea-relay.service"
    if args[0] == "restart":
        marker = root / "restart-count"
        count = int(marker.read_text()) + 1 if marker.exists() else 1
        marker.write_text(str(count))
        if failure == "restart" and count == 1:
            raise SystemExit(1)
        sock = root / "runtime/rea.sock"
        sock.parent.mkdir(exist_ok=True)
        sock.unlink(missing_ok=True)
        if os.environ.get("DELAY_RECOVERY") and count > 1:
            subprocess.Popen([sys.executable, "-c",
                              "import pathlib,time,sys; time.sleep(0.2); pathlib.Path(sys.argv[1]).touch()",
                              str(sock)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            sock.touch()
    elif args[:2] != ["is-active", "--quiet"]:
        raise SystemExit("unexpected service mutation")
elif name == "docker":
    assert args[0] == "compose"
    if args[1] == "build":
        assert args[2:] == ["bridge"]
        if failure == "build":
            raise SystemExit(1)
    elif args[1] == "up":
        assert args[-1] == "bridge"
        if not (root / "runtime/rea.sock").exists():
            print("socket_missing_at_bridge_recreate", file=sys.stderr)
            raise SystemExit(1)
        assert "--no-deps" in args and "--force-recreate" in args
    elif args[1] == "exec":
        assert args[2:5] == ["-T", "bridge", "python"]
        if failure == "verification":
            raise SystemExit(1)
    elif args[1:] != ["config", "--quiet"]:
        raise SystemExit("unexpected Docker command")
else:
    raise SystemExit("unexpected command")
"""


@pytest.fixture
def operator(tmp_path):
    src, rea, agent = (tmp_path / name for name in ("checkout", "rea", "agent"))
    for folder in (src / ".git", src / "host", src / "deploy/rea-vault",
                   rea / "managed", agent, tmp_path / "bin"):
        folder.mkdir(parents=True, exist_ok=True)
    target = agent / "rea_relay.py"
    target.write_text(OLD)
    (agent / "rea_store.py").write_text("reviewed store\n")
    (src / "host/rea_relay.py").write_text(NEW)
    unit = tmp_path / "crew-rea-relay.service"
    unit.write_text("reviewed unit\n")
    # Production paths and root-only install ownership stay fixed in the real
    # script; only the disposable test copy substitutes this temporary host.
    source = SCRIPT.read_text() if SCRIPT.exists() else ""
    source = source.replace('[[ "$EUID" == 0 ]]', '[[ 1 == 1 ]]')
    for before, after in [
        ("/opt/crew-dev-bridge", str(src)),
        ("/opt/rea-lite", str(rea)),
        ("/usr/local/lib/crew-rea", str(agent)),
        ("/etc/systemd/system/crew-rea-relay.service", str(unit)),
        ("/run/crew-rea/rea.sock", str(tmp_path / "runtime/rea.sock")),
    ]:
        source = source.replace(before, after)
    # The fixture models socket readiness with a marker and invokes command
    # doubles via Python functions, compatible with noexec temporary mounts.
    source = source.replace("[[ -S ", "[[ -f ")
    import shlex
    functions = "\n".join(
        f'{name}() {{ {shlex.quote(sys.executable)} {shlex.quote(str(tmp_path / "bin" / name))} "$@"; }}'
        for name in ("git", "install", "systemctl", "docker")
    )
    source = source.replace("set -Eeuo pipefail", "set -Eeuo pipefail\n" + functions)
    script = src / "deploy/rea-vault/upgrade-analysis.sh"
    script.write_text(source)
    for name in ("git", "install", "systemctl", "docker"):
        executable = tmp_path / "bin" / name
        executable.write_text(COMMAND_DOUBLE)
        executable.chmod(0o755)
    env = {**os.environ, "FIXTURE_ROOT": str(tmp_path),
           "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]}

    def run(**changes):
        assert SCRIPT.exists(), "Reviewed host upgrade script is not implemented"
        return subprocess.run(["bash", str(script)], env={**env, **changes},
                              text=True, capture_output=True, timeout=20)

    def calls():
        log = tmp_path / "commands.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    return {"run": run, "target": target,
            "backup": agent / "rea_relay.py.before-analysis-views",
            "calls": calls}


def test_upgrade_installs_verified_source_and_retains_original(operator):
    result = operator["run"]()
    assert result.returncode == 0, result.stdout + result.stderr + str(operator["calls"]())
    assert operator["target"].read_text() == NEW
    assert operator["backup"].read_text() == OLD
    calls = operator["calls"]()
    assert ["docker", ["compose", "build", "bridge"]] in calls
    assert any(name == "docker" and args[1] == "exec" for name, args in calls)
    assert "PASS" in result.stdout


def test_upgrade_refuses_installed_drift_before_mutation(operator):
    operator["target"].write_text("local operator modification\n")
    result = operator["run"]()
    assert result.returncode != 0
    assert operator["target"].read_text() == "local operator modification\n"
    assert not operator["backup"].exists()
    assert not any(name in ("systemctl", "docker", "install")
                   for name, _ in operator["calls"]())


def test_upgrade_never_overwrites_a_previous_backup(operator):
    operator["backup"].write_text("previous recovery copy\n")
    result = operator["run"]()
    assert result.returncode != 0
    assert operator["backup"].read_text() == "previous recovery copy\n"
    assert operator["target"].read_text() == OLD
    assert not any(name in ("systemctl", "docker", "install")
                   for name, _ in operator["calls"]())


@pytest.mark.parametrize("changes", [{"FIXTURE_DIRTY": "1"},
                                     {"FIXTURE_BRANCH": "feature/unreviewed"}])
def test_upgrade_requires_reviewed_clean_main(operator, changes):
    result = operator["run"](**changes)
    assert result.returncode != 0
    assert operator["target"].read_text() == OLD
    assert not operator["backup"].exists()


def test_build_failure_keeps_existing_relay_running(operator):
    result = operator["run"](FAIL_AT="build")
    assert result.returncode != 0
    assert operator["target"].read_text() == OLD
    assert not operator["backup"].exists()
    assert not any(name == "systemctl" for name, _ in operator["calls"]())


@pytest.mark.parametrize("stage", ["restart", "verification"])
def test_failed_upgrade_restores_relay_and_rebinds_bridge_socket(operator, stage):
    result = operator["run"](FAIL_AT=stage)
    assert result.returncode != 0
    assert operator["target"].read_text() == OLD
    assert operator["backup"].read_text() == OLD
    calls = operator["calls"]()
    assert sum(name == "systemctl" and args[0] == "restart"
               for name, args in calls) == 2
    assert calls[-1][0] == "docker"
    assert calls[-1][1][1] == "up"
    assert "PASS" not in result.stdout
    assert "restor" in result.stderr.lower()


def test_failed_upgrade_waits_for_recovery_socket(operator):
    result = operator["run"](FAIL_AT="verification", DELAY_RECOVERY="1")
    assert result.returncode != 0
    assert operator["target"].read_text() == OLD
    assert "socket_missing_at_bridge_recreate" not in result.stderr
