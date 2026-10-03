"""Version freeze: record a SHA-256 of each signal's source (plus shared helpers) and its params.
Evaluation refuses to run if any hash differs. Line endings are normalized so Windows checkouts match.
CLI: python -m quant.freeze   (writes a NEW registry file; refuses to overwrite an existing one)"""
import hashlib, inspect, json, sys, time
from pathlib import Path
from .signals import ALL, common

DEFAULT_REGISTRY = Path(__file__).resolve().parent / 'registry' / 'signals-v1.json'


class FrozenMismatch(Exception):
    pass


def source_hash(module):
    data = Path(inspect.getfile(module)).read_bytes().replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def entries(signals=ALL):
    return [dict(name=s.NAME, version=s.VERSION, file=Path(inspect.getfile(s)).name, sha256=source_hash(s),
                 params=s.PARAMS, features=list(s.FEATURES), reason=s.REASON) for s in signals]


def write(path=DEFAULT_REGISTRY, signals=ALL):
    path = Path(path)
    if path.exists():
        raise FrozenMismatch('%s already exists; frozen registries are never overwritten' % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(frozen_at=time.time(), common_sha256=source_hash(common), signals=entries(signals))
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n')
    return doc


def verify(path=DEFAULT_REGISTRY, signals=ALL):
    path = Path(path)
    if not path.exists():
        raise FrozenMismatch('no frozen registry at %s' % path)
    doc = json.loads(path.read_text())
    if doc['common_sha256'] != source_hash(common):
        raise FrozenMismatch('signals/common.py changed since freeze')
    frozen = {e['name']: e for e in doc['signals']}
    for e in entries(signals):
        f = frozen.get(e['name'])
        if f is None or f['sha256'] != e['sha256'] or f['version'] != e['version'] or f['params'] != json.loads(json.dumps(e['params'])):
            raise FrozenMismatch('%s differs from frozen registry; bump VERSION and freeze a new registry' % e['name'])
    return doc


if __name__ == '__main__':
    print(json.dumps(write(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REGISTRY), indent=2))
