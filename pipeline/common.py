"""Shared deterministic I/O, provenance and lifecycle guards."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if line.strip():
                yield index, json.loads(line)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def assert_mount(path):
    p = Path(path).resolve()
    if p.parts[:2] == ("/", "Volumes"):
        mount = Path(*p.parts[:3])
        if not mount.is_mount():
            raise RuntimeError("External disk is not mounted; no replacement path created")


def write(path, value):
    p = Path(path)
    assert_mount(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_name(p.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(p)


def fresh_output(config, stage_number):
    project = Path(config["paths"]["project_root"]).resolve()
    output = Path(config["paths"]["output"]).resolve()
    parent = project / "artifacts" / stage_number
    if output == parent or parent not in output.parents:
        raise ValueError("New output must be a named batch under " + str(parent))
    assert_mount(output)
    if output.exists():
        raise FileExistsError("Output already exists; no overwrite or implicit retry: " + str(output))
    return output


def require_unpaused(paths):
    for path in paths:
        if Path(path).exists():
            raise RuntimeError("Pause marker is present; never cleared automatically: " + str(path))


@contextlib.contextmanager
def lock(path):
    import fcntl
    path = Path(path)
    assert_mount(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "started_at": stamp()}))
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def credentials(config):
    """Read only on explicit execution. Never write values to configuration."""
    provider = config["provider"]
    key = os.environ.get(provider["api_key_env"])
    if not key:
        source = Path(provider["env_file"])
        if source.is_file():
            for raw in source.read_text().splitlines():
                line = raw.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if line and not line.startswith("#") and "=" in line:
                    name, value = line.split("=", 1)
                    if name.strip() == provider["api_key_env"]:
                        key = value.strip().strip("\"'")
    if not key:
        raise RuntimeError("Provider credential is missing (value not logged)")
    return key
