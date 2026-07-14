# blobstore

A mini S3 in plain Python. I wrote
it to understand how object stores (buckets, blobs, REST) actually work, from
the socket up: the HTTP server is hand-rolled, storage is content-addressed
(blobs live under their SHA-256, so identical content is stored once and the
hash doubles as the ETag), and each bucket is just a flat key→metadata map.

## Run

```sh
python server.py          # http://localhost:8333
python client.py --help
```

```sh
python client.py mkbucket media
python client.py put photo.jpg -b media -k holiday/photo.jpg
python client.py ls -b media --prefix holiday/
curl -si localhost:8333/buckets/media/objects/holiday/photo.jpg
```

It can also host a static website straight out of a bucket:

```sh
python client.py sync ./mysite index.html css img -b site
# -> http://localhost:8333/site/
```

## How data lives on disk

Uploading two files:

```sh
python client.py put photo.jpg -b media -k holiday/photo.jpg
python client.py put notes.txt -b media -k holiday/notes.txt
```

produces this layout:

```
data/
  _blobs/
    74/03/740388e1…      <- the bytes, named by their SHA-256
    ba/58/ba58828b…
  media/
    _index.json          <- the bucket: a flat key -> metadata map
```

and `_index.json` is plain JSON:

```json
{
  "holiday/photo.jpg": {
    "etag": "740388e1…",
    "size": 51234,
    "content_type": "image/jpeg",
    "created": 1784027153,
    "user_meta": {}
  },
  "holiday/notes.txt": { "…": "…" }
}
```

Two things to notice: the key `holiday/photo.jpg` is one string — there is no
`holiday/` directory anywhere, prefixes just imitate one — and the `etag` is
also the blob's address in `_blobs/`, so identical content is stored once no
matter how many keys point at it.
