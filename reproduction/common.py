"""Release I/O; no experiment content is synthesized here."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load_native():
    import importlib.util
    path = ROOT / 'research_runners/diagnosis/native_runtime.py'
    spec = importlib.util.spec_from_file_location('released_native_diagnosis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONTRACT = ROOT / 'configs/diagnosis'
    return module
