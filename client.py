"""blobstore CLI — talk to a running blobstore server (see server.py).

    python client.py buckets
    python client.py mkbucket media
    python client.py put photo.jpg -b media -k holiday/photo.jpg
    python client.py get holiday/photo.jpg -b media -o out.jpg
    python client.py ls -b media --prefix holiday/
    python client.py rm holiday/photo.jpg -b media
    python client.py sync <root> <subpath>... [-b site]

`sync` uploads files keyed by their path relative to <root>, so relative links
inside uploaded HTML keep working when served from /site/.
"""
# Copyright (c) 2026 Benjamin Lang. All rights reserved.

import argparse
import http.client
import mimetypes
import sys
from pathlib import Path

HOST = "localhost"
PORT = 8333


def request(method, path, body=b"", headers=None):
    """One HTTP exchange. Returns (status, headers, body)."""
    conn = http.client.HTTPConnection(HOST, PORT)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    return resp.status, dict(resp.getheaders()), resp.read()


def _guess_type(filename):
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _object_path(bucket, key):
    return f"/buckets/{bucket}/objects/{key}"


def cmd_buckets(args):
    _status, _headers, body = request("GET", "/buckets/")
    print(body.decode())


def cmd_mkbucket(args):
    status, _headers, body = request("PUT", f"/buckets/{args.name}")
    print(f"{status} {body.decode()}".strip())
    return 0 if status in (201, 409) else 1


def cmd_put(args):
    path = Path(args.file)
    key = args.key or path.name
    status, headers, body = request(
        "PUT", _object_path(args.bucket, key), body=path.read_bytes(),
        headers={"Content-Type": _guess_type(path.name)})
    if status in (200, 201):
        print(f"{status} {key}  etag={headers.get('ETag', '?')}")
        return 0
    print(f"{status} {body.decode()}", file=sys.stderr)
    return 1


def cmd_get(args):
    status, _headers, body = request("GET", _object_path(args.bucket, args.key))
    if status != 200:
        print(f"{status} {body.decode()}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_bytes(body)
    else:
        sys.stdout.buffer.write(body)
    return 0


def cmd_ls(args):
    q = f"?prefix={args.prefix}&marker={args.marker}&limit={args.limit}"
    status, headers, body = request("GET", f"/buckets/{args.bucket}/objects/{q}")
    if status != 200:
        print(f"{status} {body.decode()}", file=sys.stderr)
        return 1
    print(body.decode())
    if headers.get("X-Truncated") == "true":
        print("(truncated — repeat with --marker <last key>)", file=sys.stderr)
    return 0


def cmd_rm(args):
    status, _headers, body = request("DELETE", _object_path(args.bucket, args.key))
    if status != 204:
        print(f"{status} {body.decode()}", file=sys.stderr)
        return 1
    return 0


def cmd_sync(args):
    """Upload files under <root>/<subpath>… keyed relative to <root>."""
    root = Path(args.root).resolve()
    request("PUT", f"/buckets/{args.bucket}")   # 201 or 409 — both fine

    files = []
    for sub in args.subpaths:
        p = root / sub
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(f for f in p.rglob("*") if f.is_file())
        else:
            print(f"skipping {p} (not found)", file=sys.stderr)

    uploaded = 0
    for f in files:
        key = f.relative_to(root).as_posix()
        if "/." in f"/{key}" or key.startswith("data/"):
            continue                            # dotfiles, local state
        status, _headers, body = request(
            "PUT", _object_path(args.bucket, key), body=f.read_bytes(),
            headers={"Content-Type": _guess_type(f.name)})
        if status in (200, 201):
            uploaded += 1
            print(f"  {status} {key}")
        else:
            print(f"  {status} {key}: {body.decode()}", file=sys.stderr)
    print(f"{uploaded}/{len(files)} files uploaded to bucket {args.bucket!r}")
    return 0 if uploaded == len(files) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("buckets", help="list buckets")

    p = sub.add_parser("mkbucket", help="create a bucket")
    p.add_argument("name")

    p = sub.add_parser("put", help="upload a file")
    p.add_argument("file")
    p.add_argument("-b", "--bucket", default="media")
    p.add_argument("-k", "--key", help="object key (default: file name)")

    p = sub.add_parser("get", help="download an object")
    p.add_argument("key")
    p.add_argument("-b", "--bucket", default="media")
    p.add_argument("-o", "--output", help="write to file (default: stdout)")

    p = sub.add_parser("ls", help="list keys in a bucket")
    p.add_argument("-b", "--bucket", default="media")
    p.add_argument("--prefix", default="")
    p.add_argument("--marker", default="")
    p.add_argument("--limit", default="1000")

    p = sub.add_parser("rm", help="delete an object")
    p.add_argument("key")
    p.add_argument("-b", "--bucket", default="media")

    p = sub.add_parser("sync", help="upload a directory tree (website mode)")
    p.add_argument("root", help="keys are file paths relative to this directory")
    p.add_argument("subpaths", nargs="+", help="files/dirs under root to upload")
    p.add_argument("-b", "--bucket", default="site")

    args = parser.parse_args(argv)
    handler = {"buckets": cmd_buckets, "mkbucket": cmd_mkbucket, "put": cmd_put,
               "get": cmd_get, "ls": cmd_ls, "rm": cmd_rm, "sync": cmd_sync}[args.cmd]
    return handler(args) or 0


if __name__ == "__main__":
    sys.exit(main())
