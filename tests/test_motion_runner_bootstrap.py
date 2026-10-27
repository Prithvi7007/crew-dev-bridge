"""Motion-for-React preprovisioning must not weaken the isolated test runner."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_motion_is_pinned_for_trusted_image_build_only():
    dockerfile = (ROOT / "runner/Dockerfile").read_text(encoding="utf-8")
    assert "ARG CREW_MOTION_VERSION=14.6.0" in dockerfile
    assert 'motion@${CREW_MOTION_VERSION}' in dockerfile
    assert "npm install --no-save --no-package-lock --ignore-scripts" in dockerfile
    assert "node_modules/motion/package.json" in dockerfile
    assert dockerfile.index("RUN cd /opt/frontend && npm ci") < dockerfile.index("ARG CREW_MOTION_VERSION")
    assert dockerfile.index("ARG CREW_MOTION_VERSION") < dockerfile.index("USER 0:10001")


def test_runner_remains_offline_without_dependency_install_commands():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    runner = compose.split("  runner:", 1)[1]
    worker = (ROOT / "runner/worker.py").read_text(encoding="utf-8")
    assert "network_mode: none" in runner
    assert "read_only: true" in runner
    assert "node_modules').symlink_to('/opt/frontend/node_modules'" in worker
    assert "['npm', 'run', 'build']" in worker
    assert "npm install" not in worker
    assert "npm ci" not in worker
    assert "_drop_test_privileges" in worker
