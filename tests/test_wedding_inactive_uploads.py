"""Photo-upload launch gate: isolated unit tests, no production reads/writes."""
import pytest

from host import wedding_release_agent as release


def test_photo_feature_is_inactive_and_does_not_require_upload_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "UPLOADS", tmp_path / "uploads")
    invoked = []
    def fake_postgres(argv, **kwargs):
        invoked.append(list(argv))
        return "0"
    monkeypatch.setattr(release, "postgres", fake_postgres)
    release.require_inactive_photo_uploads()
    assert len(invoked) == 1
    assert invoked[0] == [
        "psql", "-d", "wedding_db", "-Atqc",
        "SELECT COUNT(*) FROM public.photo_uploads",
    ]


@pytest.mark.parametrize("value", ["1", "18", "", "garbled", "-1"])
def test_photo_records_block_release_even_when_directory_missing(monkeypatch, tmp_path, value):
    monkeypatch.setattr(release, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(release, "postgres", lambda *args, **kwargs: value)
    with pytest.raises(release.Stop, match="photo_upload_records_require_manual_review"):
        release.require_inactive_photo_uploads()


def test_existing_empty_upload_directory_blocks_unsafe_release(monkeypatch, tmp_path):
    photos = tmp_path / "uploads"
    photos.mkdir()
    monkeypatch.setattr(release, "UPLOADS", photos)
    monkeypatch.setattr(release, "postgres", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("No SQL should run if upload directory exists")))
    with pytest.raises(release.Stop, match="photo_storage_requires_manual_review"):
        release.require_inactive_photo_uploads()


def test_symlinked_upload_directory_is_rejected(monkeypatch, tmp_path):
    photos = tmp_path / "uploads"
    photos.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(release, "UPLOADS", photos)
    with pytest.raises(release.Stop, match="photo_storage_requires_manual_review"):
        release.require_inactive_photo_uploads()


@pytest.mark.parametrize("file", [
    "app/photos/routes.py", "app/uploads/routes.py",
    "app/gallery/routes.py", "app/static/photo-picker.js",
])
def test_adding_photo_feature_requires_operator_review(monkeypatch, file):
    def git(*args, **kwargs):
        return file if "--name-only" in args else ""
    monkeypatch.setattr(release, "git", git)
    with pytest.raises(release.Stop, match="photo_feature_changes_require_manual_review"):
        release.allowed_changes("a" * 40, "b" * 40)


def test_database_backup_does_not_require_nonexistent_media(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "BACKUPS", tmp_path)
    def dump(argv, **kwargs):
        assert argv[:3] == ["pg_dump", "-Fc", "--dbname"]
        kwargs["stdout"].write(b"synthetic-test-only-pg-dump")
        return ""
    monkeypatch.setattr(release, "postgres", dump)
    saved = release.backup("offline-backup")
    assert saved.exists()
    assert (tmp_path / "offline-backup.sha256").exists()
    assert not (tmp_path / "offline-backup.uploads.tar.gz").exists()
