"""Fill the raw caches: legacy files (with their header facts) and 3DEP crops per cell from EPT.

Legacy files are taken, in order of preference, from the cache, from a seed directory, or from OT. A seed directory
mirrors the bucket below IN_2011_2013/ (<zone>/<folder>/<name>); $ILCD_SEED_LEGACY lists such directories (os.pathsep
separated), e.g. earlier downloads. Seeded files must match the listed size and are hard-linked when possible.
"""

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import laspy
import numpy as np
import pyarrow as pa

from ..common import config, contracts, crs, gpstime, grid, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from . import catalog, http

log = get_logger(__name__)


def legacy_path(file_id: str) -> Path:
    return paths.cache_legacy(catalog.legacy_file(file_id)["url"])


def _seed(key_tail: str, size: int, dest: Path) -> bool:
    for d in filter(None, os.environ.get("ILCD_SEED_LEGACY", "").split(os.pathsep)):
        src = Path(d) / key_tail
        if src.is_file() and src.stat().st_size == size:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dest)
            except OSError:
                shutil.copyfile(src, dest)
            log.info("seeded %s from %s", dest.name, src)
            return True
    return False


def fetch_legacy(file_id: str) -> Path:
    """Make sure the canonical copy of a legacy file is in the cache (size-checked); return its path."""
    row = catalog.legacy_file(file_id)
    dest = paths.cache_legacy(row["url"])
    size = int(row["size_bytes"])
    if dest.is_file() and dest.stat().st_size == size:
        return dest
    tail = row["url"].split("/IN_2011_2013/", 1)[1]
    if not _seed(tail, size, dest):
        http.download(row["url"], dest, expected_size=size)
    return dest


def time_encoding_override(delivery_id: str) -> str | None:
    """The delivery's gps_time_encoding from config/deliveries.yaml, when its headers carry a wrong encoding bit."""
    return config.load_delivery_overrides().get(delivery_id, {}).get("gps_time_encoding")


def adjusted_bit(las, delivery_id: str) -> bool:
    """Whether the file's GPS times are adjusted standard: the header's global-encoding bit, unless
    config/deliveries.yaml sets gps_time_encoding for the delivery (docs/design.md section 5)."""
    o = time_encoding_override(delivery_id)
    if o is not None:
        return o == "adjusted_standard"
    return las.header.global_encoding.gps_time_type == laspy.header.GpsTimeType.STANDARD


def header_facts(file_id: str, path: Path, params) -> dict:
    """One legacy_headers row: hash, counts, time type (bit checked against the range), scales."""
    las = laspy.read(path)
    t = np.asarray(las.gps_time)
    bit = adjusted_bit(las, catalog.legacy_file(file_id)["delivery_id"])
    ttype, n_bad = gpstime.classify_points(bit, t, params["data_src.points.max_invalid_time_share"])
    ok = gpstime.valid(bit, t)
    cls = np.asarray(las.classification)
    return {
        "file_id": file_id,
        "sha256": io.sha256_file(path),
        "n_points": int(len(las.points)),
        "point_format": int(las.header.point_format.id),
        "gps_time_type": ttype,
        "n_invalid_time": n_bad,
        "gps_min_s": float(t[ok].min()),
        "gps_max_s": float(t[ok].max()),
        "scale_xy": float(las.header.scales[0]),
        "scale_z": float(las.header.scales[2]),
        "n_psid": int(np.unique(np.asarray(las.point_source_id)).size),
        "class12_share": float(np.mean(cls == 12)),
        "copies_identical": None,
        "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def fetch_legacy_step(file_id: str, params) -> Path:
    """Fetch one legacy file and write files/<file_id>/legacy_header.parquet (with manifest)."""
    out_dir = paths.file_dir(file_id)
    enc = time_encoding_override(catalog.legacy_file(file_id)["delivery_id"])
    sp = {"gps_time_encoding": enc} if enc else {}
    with Step("data_src.fetch_legacy", out_dir=out_dir, params=sp, unit=file_id) as step:
        path = fetch_legacy(file_id)
        facts = header_facts(file_id, path, params)
        step.input(path, sha256=facts["sha256"])
        t = pa.Table.from_pylist([facts], schema=contracts.get("legacy_headers").arrow_schema())
        step.output(io.write_table(t, out_dir / "legacy_header.parquet", "legacy_headers"), "legacy_headers")
    return path


# 3DEP --------------------------------------------------------------------------------------------------------------


def modern_batches_for(cell: str) -> list[str]:
    mb = catalog.read_modern_batches()
    from shapely.geometry import box

    return sorted(mb[mb.intersects(box(*grid.cell_bounds(cell)))]["batch"])


def _merc_bounds(cell: str, pad: float) -> tuple[float, float, float, float]:
    """EPSG:3857 box around a cell: project -> lon/lat (taken as NAD83, null shift) -> Web Mercator."""
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    xs = [xmin - pad, xmax + pad, xmin - pad, xmax + pad]
    ys = [ymin - pad, ymin - pad, ymax + pad, ymax + pad]
    lon, lat = crs.transformer(crs.PROJECT_CRS, "EPSG:6318").transform(xs, ys)
    mx, my = crs.transformer("EPSG:4326", "EPSG:3857").transform(lon, lat)
    return min(mx), min(my), max(mx), max(my)


def fetch_modern(cell: str, batch: str, params) -> Path | None:
    """EPT points of one batch around a cell, cached as LAZ in EPT's own frame (EPSG:3857, metres).
    Returns None (and leaves an .empty marker) when the batch has no points there."""
    import pdal

    dest = paths.cache_modern(batch, cell)
    empty = dest.with_suffix(".empty")
    if dest.is_file():
        return dest
    if empty.is_file():
        return None
    b = _merc_bounds(cell, params["data_src.ept.pad_m"])
    url = f"{params['data_src.ept.base_url'].rstrip('/')}/{batch}/ept.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with io.atomic_path(dest) as tmp:
        pipe = pdal.Pipeline(
            json.dumps(
                [
                    {"type": "readers.ept", "filename": url, "bounds": f"([{b[0]}, {b[2]}], [{b[1]}, {b[3]}])"},
                    {
                        "type": "writers.las",
                        "filename": str(tmp),
                        "compression": "laszip",
                        "minor_version": 4,
                        "dataformat_id": 6,
                        "forward": "all",
                        "extra_dims": "all",
                        "a_srs": "EPSG:3857",
                    },
                ]
            )
        )
        n = _execute_with_retry(pipe, f"{batch} {cell}")
        if n == 0:
            raise _Empty()
    log.info("cell %s batch %s: %d points", cell, batch, n)
    return dest


class _Empty(Exception):
    pass


def _execute_with_retry(pipe, what: str, tries: int = 4) -> int:
    """PDAL execute; EPT tiles on S3 sometimes fail to read under load ("Could not read from"), so retry with
    backoff. Other errors are raised at once."""
    import time

    for i in range(tries):
        try:
            return pipe.execute()
        except RuntimeError as e:
            if "Could not read" not in str(e) or i == tries - 1:
                raise
            log.warning("EPT read failed for %s (%s); retry %d", what, str(e)[:120], i + 1)
            time.sleep(20 * 2**i)
    raise AssertionError("unreachable")


def fetch_modern_step(cell: str, params) -> list[Path]:
    """Fetch every batch intersecting a cell; writes cells/<cell>/data_src.fetch_modern.manifest.json."""
    out = []
    with Step(
        "data_src.fetch_modern", out_dir=paths.cell_dir(cell), params=params.section("data_src"), unit=cell
    ) as step:
        for batch in modern_batches_for(cell):
            try:
                p = fetch_modern(cell, batch, params)
            except _Empty:
                paths.cache_modern(batch, cell).with_suffix(".empty").touch()
                p = None
            if p is not None:
                step.input(p, hash_file=True)
                out.append(p)
        if not out:
            step.empty("no 3DEP points in this cell")
    return out
