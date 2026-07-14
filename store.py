"""blobstore storage engine: content-addressed blobs + per-bucket key indexes.

Layout on disk (anchored next to this file, independent of cwd):

    data/_blobs/ab/cd/<sha256>   blobs, named by content hash (doubles as ETag)
    data/<bucket>/_index.json    flat key -> metadata map, one per bucket
"""
# Copyright (c) 2026 Benjamin Lang. All rights reserved.

import hashlib
import json
import re
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
BLOBS = DATA / "_blobs"
BUCKET_NAME_RE = re.compile(r"^[a-z0-9-]+$")  # no "_": reserves _blobs & friends


def _blob_path(etag: str) -> Path:
    """Two-level fan-out (like git objects) keeps directories small."""
    return BLOBS / etag[:2] / etag[2:4] / etag


def _write_blob(data: bytes) -> str:
    """Store bytes under their sha256; return the hash. Same content = same
    file, so the existence check IS the deduplication."""
    etag = hashlib.sha256(data).hexdigest()
    path = _blob_path(etag)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return etag


def _index_path(bucket: str) -> Path:
    return DATA / bucket / "_index.json"


def _bucket_exists(bucket: str) -> bool:
    return (DATA / bucket).is_dir()


def _load_index(bucket: str) -> dict:
    if not _bucket_exists(bucket):
        raise KeyError(f"bucket {bucket!r} does not exist")
    path = _index_path(bucket)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_index(bucket: str, index: dict) -> None:
    _index_path(bucket).write_text(json.dumps(index, indent=2))


def create_bucket(name: str) -> None:
    """Create a bucket. ValueError for an invalid name, KeyError if it exists."""
    if not BUCKET_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid bucket name {name!r}")
    path = DATA / name
    if path.exists():
        raise KeyError(f"bucket {name!r} already exists")
    path.mkdir(parents=True, exist_ok=False)
    _save_index(name, {})


def list_buckets() -> list:
    """Sorted names of all buckets."""
    if not DATA.exists():
        return []
    return sorted(d.name for d in DATA.iterdir()
                  if d.is_dir() and not d.name.startswith("_"))


def delete_bucket(name: str) -> None:
    """Delete an empty bucket. KeyError if absent, ValueError if non-empty."""
    path = DATA / name
    if not path.exists():
        raise KeyError(f"bucket {name!r} does not exist")
    if _load_index(name):
        raise ValueError(f"bucket {name!r} is not empty")
    _index_path(name).unlink(missing_ok=True)
    path.rmdir()


def put(bucket: str, key: str, data: bytes,
        content_type: str = "application/octet-stream",
        user_meta: dict | None = None) -> dict:
    """Store data under bucket/key and return its metadata entry.

    PUT semantics: an existing key is replaced whole, metadata included.
    """
    index = _load_index(bucket)
    etag = _write_blob(data)
    entry = {
        "etag": etag,
        "size": len(data),
        "content_type": content_type,
        "created": int(time.time()),
        "user_meta": user_meta or {},
    }
    index[key] = entry
    _save_index(bucket, index)
    return entry


def get(bucket: str, key: str) -> tuple:
    """Return (data, metadata). KeyError if bucket or key is absent."""
    entry = _load_index(bucket)[key]
    return _blob_path(entry["etag"]).read_bytes(), entry


def head(bucket: str, key: str) -> dict:
    """Return metadata only — the blob is never read."""
    return _load_index(bucket)[key]


def delete(bucket: str, key: str) -> None:
    """Remove the key. The blob stays: other keys may share it."""
    index = _load_index(bucket)
    del index[key]
    _save_index(bucket, index)


def list_keys(bucket: str, prefix: str = "", marker: str = "",
              limit: int = 1000) -> tuple:
    """Return (keys, truncated): sorted keys matching prefix, strictly after
    marker, at most limit of them. This is S3's entire "folder" illusion."""
    keys = [k for k in sorted(_load_index(bucket))
            if k.startswith(prefix) and k > marker]
    return keys[:limit], len(keys) > limit
