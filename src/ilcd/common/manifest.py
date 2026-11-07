"""Provenance manifests (docs/design.md section 10).

    with Step("flightlines.passes", unit=file_id, params=p.section("flightlines"), out_dir=d) as step:
        step.input(raw_path)
        ...
        step.output(write_table(t, d / "passes.parquet", "passes"), "passes")
        if t.num_rows == 0:
            step.empty("no pass with enough points")

On success the manifest is written as <out_dir>/<step>.manifest.json. On an exception nothing is written, so a
missing manifest always means the step did not finish.
"""

import hashlib
import json
import os
import socket
import subprocess
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import ilcd

from . import paths
from .io import atomic_path, sha256_file

_PKG_DIR = Path(ilcd.__file__).resolve().parent


@cache
def code_version() -> dict:
    """Package version and, when installed from a git checkout, its commit and whether the tree is dirty."""
    commit = dirty = None
    try:
        commit = subprocess.run(
            ["git", "-C", str(_PKG_DIR), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(_PKG_DIR), "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {"version": ilcd.__version__, "commit": commit, "dirty": dirty}


def params_sha256(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Step:
    def __init__(self, step: str, *, out_dir, params: dict, unit: str | None = None):
        self.step, self.out_dir, self.params, self.unit = step, Path(out_dir), params, unit
        self.inputs, self.outputs, self.status, self.message = [], [], "ok", None

    def __enter__(self):
        self.started = _now()
        return self

    def input(self, path, contract: str | None = None, sha256: str | None = None, hash_file: bool = False):
        """Record an input. Contract files are hashed; raw files pass their catalog sha256 (or hash_file=True)."""
        p = Path(path)
        if sha256 is None and (contract is not None or hash_file):
            sha256 = sha256_file(p)
        self.inputs.append(
            {
                "path": paths.relative(p),
                "sha256": sha256,
                "contract": contract,
                "bytes": p.stat().st_size if p.exists() else None,
            }
        )

    def output(self, path, contract: str | None = None):
        p = Path(path)
        self.outputs.append(
            {"path": paths.relative(p), "sha256": sha256_file(p), "contract": contract, "bytes": p.stat().st_size}
        )
        return p

    def empty(self, message: str):
        """Mark the step's outputs as valid but empty (e.g. a forest cell without reference surfaces)."""
        self.status, self.message = "empty", message

    @property
    def path(self) -> Path:
        return self.out_dir / f"{self.step}.manifest.json"

    def document(self) -> dict:
        doc = {
            "step": self.step,
            "status": self.status,
            "code": code_version(),
            "params_sha256": params_sha256(self.params),
            "params": self.params,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "started_utc": self.started,
            "finished_utc": _now(),
            "host": socket.gethostname(),
            "slurm_job": os.environ.get("SLURM_JOB_ID"),
        }
        if self.unit is not None:
            doc["unit"] = self.unit
        if self.message is not None:
            doc["message"] = self.message
        return doc

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            return False
        from .contracts import get

        doc = self.document()
        get("manifest").check_json(doc)
        with atomic_path(self.path) as tmp:
            tmp.write_text(json.dumps(doc, indent=1) + "\n")
        return False


def read(path) -> dict:
    from .io import read_json

    return read_json(path, "manifest")


def done(out_dir, step: str) -> bool:
    """True when the step has a manifest in out_dir (it finished at least once; inputs are not compared)."""
    return (Path(out_dir) / f"{step}.manifest.json").exists()


@cache
def _sha_at(path: Path, mtime_ns: int, size: int) -> str:
    return sha256_file(path)


def current(out_dir, step: str, params: dict) -> bool:
    """True when the step has a manifest in out_dir, made with these params, and none of its hashed inputs has
    changed since. An input is re-hashed only when it is newer than the manifest, so the check is cheap. Code changes
    are not detected: after one, recompute explicitly (run without --skip-existing)."""
    p = Path(out_dir) / f"{step}.manifest.json"
    if not p.exists():
        return False
    doc = json.loads(p.read_text())
    if doc["params_sha256"] != params_sha256(params):
        return False
    t = p.stat().st_mtime_ns
    for i in doc["inputs"]:
        if i["sha256"] is None:
            continue
        f = Path(i["path"])
        f = f if f.is_absolute() else paths.data_root() / f
        if not f.exists():
            return False
        st = f.stat()
        if st.st_mtime_ns > t and _sha_at(f, st.st_mtime_ns, st.st_size) != i["sha256"]:
            return False
    return True
