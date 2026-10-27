"""Regression tests for the Wedding agent's optional subprocess umask.

The release agent runs at private umask 077; only Git reset needs the
public-readable 022 override. An omitted override must not reach Popen as
None, because Popen rejects that value.
"""
import os
from pathlib import Path
import stat
import sys

from host import wedding_release_agent as release


def _write_with_child(destination: Path, *, umask=None):
    return release.run(
        [sys.executable, "-c",
         "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ok')",
         str(destination)],
        umask=umask,
    )


def test_default_subprocess_launches_without_invalid_none_umask(tmp_path):
    assert release.run([sys.executable, "-c", "print('ready')"]) == "ready"

    original = os.umask(0o077)
    try:
        private = tmp_path / "private.txt"
        _write_with_child(private)
    finally:
        os.umask(original)

    assert stat.S_IMODE(private.stat().st_mode) == 0o600


def test_checkout_subprocess_umask_override_is_explicit(tmp_path):
    original = os.umask(0o077)
    try:
        public = tmp_path / "public.css"
        _write_with_child(public, umask=0o022)
    finally:
        os.umask(original)

    assert stat.S_IMODE(public.stat().st_mode) == 0o644
