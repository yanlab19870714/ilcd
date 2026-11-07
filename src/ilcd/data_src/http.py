"""HTTP access for data_src: retrying session, atomic downloads, S3 bucket listing, LAS header range reads."""

import urllib.parse
import xml.etree.ElementTree as ET
from functools import cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..common.io import atomic_path
from ..common.log import get_logger

log = get_logger(__name__)
_S3 = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


@cache
def session() -> requests.Session:
    """Retries with exponential backoff on connection errors, 429 and 5xx (OT, EPT and Census all rate-limit)."""
    s = requests.Session()
    retry = Retry(
        total=8, backoff_factor=2.0, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "HEAD")
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    s.headers["User-Agent"] = "ilcd (https://github.com/yanlab19870714/ilcd)"
    return s


def download(url: str, path, expected_size: int | None = None, chunk: int = 1 << 20):
    """Stream url to path atomically; with expected_size, a short or long download is an error."""
    with session().get(url, stream=True, timeout=120) as r, atomic_path(path) as tmp:
        r.raise_for_status()
        n = 0
        with open(tmp, "wb") as f:
            for b in r.iter_content(chunk):
                f.write(b)
                n += len(b)
        if expected_size is not None and n != expected_size:
            raise OSError(f"{url}: got {n} bytes, expected {expected_size}")
    log.info("downloaded %s (%.1f MB)", url, n / 1e6)
    return path


def list_bucket(base: str, prefix: str) -> list[tuple[str, int]]:
    """(key, size) of every object under prefix of an S3-compatible bucket at base (ListObjects v1, paginated)."""
    rows, marker = [], ""
    while True:
        q = urllib.parse.urlencode({"prefix": prefix, "marker": marker, "max-keys": "1000"})
        r = session().get(f"{base}?{q}", timeout=120)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for c in root.findall("s:Contents", _S3):
            rows.append((c.find("s:Key", _S3).text, int(c.find("s:Size", _S3).text)))
        if root.find("s:IsTruncated", _S3).text != "true" or not rows:
            return rows
        marker = rows[-1][0]
