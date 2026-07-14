"""blobstore server — a mini S3: an HTTP object store over store.py.

Single-threaded, stdlib-only, one request per connection.

Run:   python server.py
Try:   curl -si -X PUT localhost:8333/buckets/media/objects/a.txt -d hello
       curl -s  localhost:8333/buckets/media/objects/a.txt
       curl -s 'localhost:8333/buckets/media/objects/?prefix=a'
Or use the CLI:  python client.py --help

Routes:
    /site/<key>                       GET: static website served from the
                                      "site" bucket ("/" -> index.html)
    /buckets/                         GET: list buckets
    /buckets/<b>                      PUT: create, DELETE: remove (if empty)
    /buckets/<b>/objects/             GET: list keys (?prefix=&marker=&limit=)
                                      POST: create, server picks the key
    /buckets/<b>/objects/<key…>       PUT / GET / HEAD / DELETE one object
"""
# Copyright (c) 2026 Benjamin Lang. All rights reserved.

import hashlib
import socket

import store

PORT = 8333

REASONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    500: "Internal Server Error",
}


def read_request(conn):
    """Read and parse one HTTP request. Returns (method, path, query, headers, body).

    recv() returns whatever bytes have arrived so far — both loops exist
    because a request can arrive fragmented at any byte boundary.
    """
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        data = conn.recv(4096)
        if not data:
            raise ConnectionError("client hung up")
        buffer += data

    head, _, body = buffer.partition(b"\r\n\r\n")
    lines = head.decode("ascii").split("\r\n")

    method, target, _version = lines[0].split(" ", 2)

    path, _, raw_query = target.partition("?")
    query = {}
    for pair in raw_query.split("&"):
        if pair:
            name, _, value = pair.partition("=")
            query[name] = value

    headers = {}
    for line in lines[1:]:
        if line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()  # names are case-insensitive

    if "content-length" in headers:
        content_length = int(headers["content-length"])
        while len(body) < content_length:
            data = conn.recv(4096)
            if not data:
                raise ConnectionError("client hung up mid-body")
            body += data

    return method, path, query, headers, body


def send_response(conn, status, headers, body):
    """Serialize and send one response."""
    # setdefault: handlers may supply their own Content-Length (HEAD must
    # report the size of the body it is NOT sending).
    headers.setdefault("Content-Length", str(len(body)))
    head = f"HTTP/1.1 {status} {REASONS[status]}\r\n"
    for name, value in headers.items():
        head += f"{name}: {value}\r\n"
    head += "\r\n"
    conn.sendall(head.encode("ascii") + body)


def _user_meta(headers):
    """X-Meta-* request headers -> user_meta dict (prefix stripped)."""
    return {name[7:]: value for name, value in headers.items()
            if name.startswith("x-meta-")}


def _object_headers(entry):
    """The response headers common to GET and HEAD on an object."""
    resp_headers = {"Content-Type": entry["content_type"],
                    "ETag": entry["etag"]}
    for name, value in entry["user_meta"].items():
        resp_headers["X-Meta-" + name] = value
    return resp_headers


def route(method, path, query, headers, body):
    """Map (method, path) onto store calls. Returns (status, headers, body)."""
    try:
        # Website mode: the "site" bucket is served under /site/ only.
        # / and /site redirect there, so the page's RELATIVE links
        # (assets/…, pages/…) resolve to /site/assets/… and hit the bucket.
        if method == "GET" and path in ("/", "/site"):
            return 301, {"Location": "/site/"}, b""
        if method == "GET" and path.startswith("/site/"):
            key = path[len("/site/"):]
            if key == "" or key.endswith("/"):
                key += "index.html"
            data, entry = store.get("site", key)
            return 200, {"Content-Type": entry["content_type"]}, data

        if not path.startswith("/buckets/"):
            return 404, {"Content-Type": "text/plain"}, b"no such route"
        bucket, sep, key = path[len("/buckets/"):].partition("/objects/")

        # Bucket-level routes (no "/objects/" in the URL)
        if not sep:
            if bucket == "" and method == "GET":
                names = store.list_buckets()
                return 200, {"Content-Type": "text/plain"}, "\n".join(names).encode()
            if method == "PUT" and bucket:
                try:
                    store.create_bucket(bucket)
                except KeyError:
                    return 409, {"Content-Type": "text/plain"}, b"bucket exists"
                return 201, {}, b""
            if method == "DELETE" and bucket:
                try:
                    store.delete_bucket(bucket)
                except ValueError:
                    return 409, {"Content-Type": "text/plain"}, b"bucket not empty"
                return 204, {}, b""
            return 405, {"Content-Type": "text/plain"}, b"method not allowed on bucket"

        # The collection: list / create-with-server-chosen-key
        if key == "":
            if method == "GET":
                keys, truncated = store.list_keys(
                    bucket,
                    prefix=query.get("prefix", ""),
                    marker=query.get("marker", ""),
                    limit=int(query.get("limit", "1000")))
                resp_headers = {"Content-Type": "text/plain"}
                if truncated:
                    resp_headers["X-Truncated"] = "true"
                return 200, resp_headers, "\n".join(keys).encode()
            if method == "POST":
                key = hashlib.sha256(body).hexdigest()  # the server picks the URI
                entry = store.put(bucket, key, body,
                                  content_type=headers.get("content-type",
                                                           "application/octet-stream"),
                                  user_meta=_user_meta(headers))
                return 201, {"ETag": entry["etag"],
                             "Location": f"/buckets/{bucket}/objects/{key}"}, b""
            return 405, {"Content-Type": "text/plain"}, b"method not allowed on collection"

        # One object: PUT / GET / HEAD / DELETE
        if method == "PUT":
            try:  # existence check via the index only — cheap
                store.head(bucket, key)
                existed = True
            except KeyError:
                existed = False
            entry = store.put(bucket, key, body,
                              content_type=headers.get("content-type",
                                                       "application/octet-stream"),
                              user_meta=_user_meta(headers))
            return (200 if existed else 201), {"ETag": entry["etag"]}, b""
        if method == "GET":
            data, entry = store.get(bucket, key)
            return 200, _object_headers(entry), data
        if method == "HEAD":
            entry = store.head(bucket, key)  # never store.get: HEAD reads no blob
            resp_headers = _object_headers(entry)
            resp_headers["Content-Length"] = str(entry["size"])
            return 200, resp_headers, b""
        if method == "DELETE":
            store.delete(bucket, key)
            return 204, {}, b""
        return 405, {"Content-Type": "text/plain"}, b"method not allowed on object"

    # The store/HTTP boundary: store exceptions become status codes here,
    # and only here.
    except KeyError as e:
        return 404, {"Content-Type": "text/plain"}, f"not found: {e}".encode()
    except ValueError as e:
        return 400, {"Content-Type": "text/plain"}, f"bad request: {e}".encode()


def serve(port: int = PORT) -> None:
    """socket -> bind -> listen -> accept -> talk."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        # Without SO_REUSEADDR the OS holds the port for ~1 min after Ctrl-C.
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen()
        print(f"blobstore listening on http://localhost:{port}  (Ctrl-C to stop)")
        while True:
            conn, _addr = srv.accept()
            # Browsers open speculative keep-alive connections and may send
            # nothing; without a timeout one idle socket wedges the whole
            # single-threaded server.
            conn.settimeout(5)
            with conn:
                try:
                    method, path, query, headers, body = read_request(conn)
                    status, resp_headers, resp_body = route(
                        method, path, query, headers, body)
                    send_response(conn, status, resp_headers, resp_body)
                except Exception as e:
                    # A bad request (or a bug) must never kill the server.
                    try:
                        send_response(conn, 500, {"Content-Type": "text/plain"},
                                      f"error: {e}".encode())
                    except Exception:
                        pass


if __name__ == "__main__":
    serve()
