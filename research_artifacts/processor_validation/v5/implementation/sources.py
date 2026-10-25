"""Hash-bound, lossless references to original files (RFC 6901 pointers)."""
import hashlib
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pointer(value, path):
    for key in path.split("/")[1:] if path else []:
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def child(ref, suffix):
    return {**ref, "json_pointer": ref.get("json_pointer", "") + suffix}


class Sources:
    def __init__(self):
        self.entries = []
        self.by_path = {}
        self.cache = {}

    @classmethod
    def from_entries(cls, entries, verify_hashes=True):
        """Reopen the source registry from a saved fact-layer trace."""
        instance = cls()
        instance.entries = entries
        instance.by_path = {e["path"]: e for e in entries}
        if verify_hashes:
            instance.assert_unchanged()
        return instance

    def add(self, path, role):
        path = str(Path(path).absolute())
        if path in self.by_path:
            entry = self.by_path[path]
            if role not in entry["roles"]:
                entry["roles"].append(role)
            return entry["source_id"]
        p = Path(path)
        entry = {"source_id": f"S{len(self.entries) + 1:04d}", "path": path,
                 "roles": [role], "exists": p.is_file()}
        if entry["exists"]:
            entry.update(sha256=digest(p), bytes=p.stat().st_size)
        self.entries.append(entry)
        self.by_path[path] = entry
        return entry["source_id"]

    def ref(self, path, role, json_pointer=""):
        return {"source_id": self.add(path, role), "json_pointer": json_pointer}

    def records(self, path, role):
        """Physical LF lines only; str.splitlines corrupts Unicode inside JSON strings."""
        p = Path(path)
        sid = self.add(p, role)
        if p.suffix == ".json":
            value = json.loads(p.read_text(encoding="utf-8"))
            self.cache[sid] = value
            for i, row in enumerate(value if isinstance(value, list) else [value]):
                yield row, {"source_id": sid, "json_pointer": f"/{i}" if isinstance(value, list) else ""}
            return
        physical = {}
        self.cache[sid] = physical
        with p.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                physical[line_number] = value
                for i, row in enumerate(value if isinstance(value, list) else [value]):
                    if not isinstance(row, dict):
                        raise ValueError(f"Non-object record: {p}:{line_number}")
                    yield row, {"source_id": sid, "line": line_number,
                                "json_pointer": f"/{i}" if isinstance(value, list) else ""}

    def resolve(self, ref):
        sid = ref["source_id"]
        entry = next(e for e in self.entries if e["source_id"] == sid)
        if sid not in self.cache:
            if "line" in ref:
                list(self.records(entry["path"], entry["roles"][0]))
            else:
                text = Path(entry["path"]).read_text(encoding="utf-8")
                self.cache[sid] = text if ref.get("representation") == "text" else json.loads(text)
        value = self.cache[sid]
        if "line" in ref:
            value = value[ref["line"]]
        value = pointer(value, ref.get("json_pointer", ""))
        if ref.get("decode_json_string"):
            value = pointer(json.loads(value), ref.get("decoded_pointer", ""))
        if "char_start" in ref:
            value = value[ref["char_start"]:ref["char_end"]]
        return value

    def assert_unchanged(self):
        for entry in self.entries:
            if entry["exists"] and digest(entry["path"]) != entry["sha256"]:
                raise ValueError("Source changed during construction: " + entry["path"])
