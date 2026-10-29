"""Restricted REA-only artifact vault; all data stays under ROOT.

The single-threaded host relay serializes vault operations. Uploaded files
become visible to REA only after full size and SHA-256 verification.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat

ROOT = Path("/opt/rea-lite/managed")
TMP_NAME = ".uploads"
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1536 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
MAX_FILES = 30
MAX_SESSIONS = 3
EXTENSIONS = (".apk", ".xapk", ".aab", ".zip", ".jar", ".asar")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
SAFE = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


class StoreError(Exception):
    pass


def valid_name(value):
    if (not isinstance(value, str) or not NAME.fullmatch(value)
            or value.startswith(".") or value in (".", "..")
            or not value.lower().endswith(EXTENSIONS)):
        raise StoreError("invalid_filename")
    return value


def valid_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise StoreError("invalid_sha256")
    return value


def valid_token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise StoreError("invalid_upload_id")
    return value


def directories():
    tmp = ROOT / TMP_NAME
    if ROOT.is_symlink() or tmp.is_symlink() or not ROOT.is_dir() or not tmp.is_dir():
        raise StoreError("vault_not_installed")
    return tmp


def regular(path, min_size=1):
    try:
        item = path.lstat()
    except FileNotFoundError as exc:
        raise StoreError("file_not_found") from exc
    if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1
            or not min_size <= item.st_size <= MAX_FILE_BYTES):
        raise StoreError("invalid_file")
    return item


def digest_file(path):
    fd = os.open(path, os.O_RDONLY | SAFE)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
            raise StoreError("invalid_file")
        hasher = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(block)
        return hasher.hexdigest()
    finally:
        os.close(fd)


def totals():
    count = used = pending = 0
    for path in ROOT.iterdir():
        if path.name == TMP_NAME:
            continue
        try:
            valid_name(path.name)
            used += regular(path).st_size
            count += 1
        except StoreError:
            continue
    tmp = ROOT / TMP_NAME
    for meta in tmp.glob("*.json"):
        try:
            if meta.is_symlink():
                raise ValueError("symlink")
            with meta.open(encoding="utf-8") as stream:
                item = json.load(stream)
            pending += int(item["size"])
        except (OSError, ValueError, TypeError, KeyError):
            raise StoreError("invalid_existing_upload")
    return count, used, pending


def list_files():
    directories()
    result = []
    for path in sorted(ROOT.iterdir()):
        try:
            valid_name(path.name)
            info = regular(path)
            result.append({"filename": path.name, "bytes": info.st_size,
                           "modified_epoch": int(info.st_mtime)})
        except StoreError:
            continue
    count, used, pending = totals()
    return {"ok": True, "directory": str(ROOT), "files": result[:MAX_FILES],
            "file_count": count, "used_bytes": used, "reserved_upload_bytes": pending,
            "max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES,
            "truncated": count > MAX_FILES}


def file_info(filename):
    directories()
    valid_name(filename)
    target = ROOT / filename
    info = regular(target)
    return {"ok": True, "filename": filename, "bytes": info.st_size,
            "sha256": digest_file(target),
            "container_path": "/work/managed/" + filename}


def paths(upload_id):
    valid_token(upload_id)
    tmp = directories()
    return tmp / (upload_id + ".json"), tmp / (upload_id + ".part")


def session(upload_id):
    meta, part = paths(upload_id)
    try:
        if meta.is_symlink() or part.is_symlink():
            raise ValueError("symlink")
        fd = os.open(meta, os.O_RDONLY | SAFE)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            item = json.load(stream)
        if not isinstance(item, dict) or set(item) != {"filename", "size", "sha256"}:
            raise ValueError("invalid metadata")
        valid_name(item["filename"])
        valid_sha(item["sha256"])
        size = item["size"]
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE_BYTES:
            raise ValueError("invalid size")
        info = regular(part, min_size=0)
        if info.st_size > size:
            raise ValueError("overrun")
    except (OSError, TypeError, ValueError, KeyError, StoreError) as exc:
        raise StoreError("upload_not_found_or_invalid") from exc
    return item, meta, part, info.st_size


def begin_upload(filename, size, sha256):
    tmp = directories()
    valid_name(filename)
    valid_sha(sha256)
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE_BYTES:
        raise StoreError("invalid_size")
    if (ROOT / filename).exists() or (ROOT / filename).is_symlink():
        raise StoreError("filename_exists")
    count, used, pending = totals()
    if count >= MAX_FILES or sum(1 for _ in tmp.glob("*.json")) >= MAX_SESSIONS:
        raise StoreError("vault_capacity_reached")
    if used + pending + size > MAX_TOTAL_BYTES:
        raise StoreError("vault_quota_exceeded")
    if shutil.disk_usage(ROOT).free < size + (1024 * 1024 * 1024):
        raise StoreError("insufficient_disk_headroom")
    upload_id = secrets.token_hex(16)
    meta, part = paths(upload_id)
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | SAFE, 0o640)
    os.close(fd)
    try:
        with meta.open("x", encoding="utf-8") as stream:
            json.dump({"filename": filename, "size": size, "sha256": sha256}, stream)
        os.chmod(meta, 0o600)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return {"ok": True, "upload_id": upload_id, "offset": 0,
            "size": size, "max_chunk_bytes": MAX_CHUNK_BYTES}


def upload_status(upload_id):
    item, _, _, offset = session(upload_id)
    return {"ok": True, "upload_id": upload_id, "filename": item["filename"],
            "offset": offset, "size": item["size"]}


def append_chunk(upload_id, offset, data_b64):
    item, _, part, current = session(upload_id)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset != current:
        raise StoreError("incorrect_offset")
    if not isinstance(data_b64, str) or len(data_b64) > 4 * ((MAX_CHUNK_BYTES + 2) // 3):
        raise StoreError("chunk_too_large")
    try:
        chunk = base64.b64decode(data_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StoreError("invalid_base64") from exc
    if not chunk or len(chunk) > MAX_CHUNK_BYTES or current + len(chunk) > item["size"]:
        raise StoreError("invalid_chunk_size")
    fd = os.open(part, os.O_WRONLY | os.O_APPEND | SAFE)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != current:
            raise StoreError("upload_modified")
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(chunk)
            stream.flush()
    finally:
        os.close(fd)
    return {"ok": True, "upload_id": upload_id, "offset": current + len(chunk),
            "size": item["size"]}


def finish_upload(upload_id):
    item, meta, part, length = session(upload_id)
    if length != item["size"]:
        raise StoreError("upload_incomplete")
    sha256 = digest_file(part)
    if sha256 != item["sha256"]:
        raise StoreError("sha256_mismatch")
    dest = ROOT / item["filename"]
    try:
        os.link(part, dest, follow_symlinks=False)  # atomic, no overwrite
    except FileExistsError as exc:
        raise StoreError("filename_exists") from exc
    part.unlink()
    meta.unlink()
    return {"ok": True, "filename": item["filename"], "bytes": length,
            "sha256": sha256, "container_path": "/work/managed/" + item["filename"]}


def cancel_upload(upload_id):
    _, meta, part, _ = session(upload_id)
    part.unlink()
    meta.unlink()
    return {"ok": True, "cancelled": True}


def delete_file(filename, expected_sha256):
    directories()
    valid_name(filename)
    valid_sha(expected_sha256)
    path = ROOT / filename
    regular(path)
    actual = digest_file(path)
    if actual != expected_sha256:
        raise StoreError("sha256_mismatch")
    path.unlink()
    return {"ok": True, "deleted": filename, "sha256": actual, "permanent": True}
