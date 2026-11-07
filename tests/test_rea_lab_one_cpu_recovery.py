"""REA Lab one-vCPU recovery contract; all assertions are offline/read-only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "deploy" / "rea-lab"


def test_single_cpu_compose_budgets_leave_room_for_other_apps():
    compose = (LAB / "compose.yaml").read_text(encoding="utf-8")
    assert 'cpus: "1.5"' not in compose
    assert 'cpus: "0.65"' in compose
    assert 'cpus: "0.10"' in compose
    assert "mem_limit: 2300m" in compose
    assert "../rea-lite/managed:/vault:ro" in compose
    assert "127.0.0.1:18766:8080" in compose
    assert "internal: true" in compose


def test_partial_install_recovery_is_bounded_and_no_rebuild_or_volume_deletion():
    script = (LAB / "recover-one-cpu.sh").read_text(encoding="utf-8")
    assert "4f1ed8ec80e89cc6162e1a707dee7006622c2e65" in script
    assert '"$DEST/compose.yaml.before-one-cpu"' in script
    assert 'git -C "$SRC" show "$BASE:deploy/rea-lab/$file" | cmp -s' in script
    assert '[[ ! -e "$AGENT/rea_relay.py.before-lab" ]]' in script
    assert 'stat -c' in script
    assert "docker image inspect rea-lab:jadx-1.5.6" in script
    assert "docker compose up -d --no-build" in script
    assert "127.0.0.1:18766/capabilities" in script
    assert "data.get(\"jadx\")" in script
    assert 'install -o root -g root -m 0644 "$SRC/host/rea_relay.py"' in script
    assert 'systemctl restart "$UNIT"' in script
    assert "docker compose up -d --build --force-recreate --no-deps bridge" in script
    assert "docker compose down" not in script
    assert "docker compose restart" not in script
    assert "docker compose up -d --build\n" not in script
    assert "rm -rf" not in script
    assert "crew.service" not in script
    assert "wedding.service" not in script
