"""Download pinned tools and schema sources, check sha256, build JSON schemas.

Output goes to .cache/bin and .cache/schemas/<group>/<kind>_<version>.json,
the layout scripts/validate.sh passes to kubeconform.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
BIN, SCHEMAS, SRC = CACHE / "bin", CACHE / "schemas", CACHE / "src"
LOCAL_XRDS = [ROOT / "infra" / "xrd.yaml"]


def read_lock() -> list[tuple[str, str, str]]:
    rows = []
    for line in (ROOT / "scripts" / "sources.lock").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            sha, kind, url = line.split()
            rows.append((sha, kind, url))
    return rows


def download(sha: str, url: str) -> Path:
    dest = SRC / sha  # name by hash so a changed pin never reuses a stale file
    if not dest.exists():
        with urllib.request.urlopen(url, timeout=60) as resp:
            dest.write_bytes(resp.read())
    actual = hashlib.sha256(dest.read_bytes()).hexdigest()
    if actual != sha:
        dest.unlink()
        raise SystemExit(f"sha256 mismatch for {url}: expected {sha}, got {actual}")
    return dest


def strict(node):
    """Mirror kubeconform's strict k8s schemas: unknown fields are errors."""
    if isinstance(node, dict):
        if node.pop("x-kubernetes-int-or-string", False):
            node.pop("type", None)
            node["anyOf"] = [{"type": "integer"}, {"type": "string"}]
        preserve = node.get("x-kubernetes-preserve-unknown-fields")
        if "properties" in node and "additionalProperties" not in node and not preserve:
            node["additionalProperties"] = False
        for value in node.values():
            strict(value)
    elif isinstance(node, list):
        for item in node:
            strict(item)
    return node


def write_schema(group: str, kind: str, version: str, schema: dict) -> None:
    schema = strict(json.loads(json.dumps(schema)))
    props = schema.setdefault("properties", {})
    props.setdefault("apiVersion", {"type": "string"})
    props.setdefault("kind", {"type": "string"})
    props.setdefault("metadata", {"type": "object"})
    out = SCHEMAS / group / f"{kind.lower()}_{version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=1))
    print(f"schema {group}/{kind} {version}")


def convert(path: Path) -> None:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    if not docs:
        raise SystemExit(f"{path}: no documents")
    for doc in docs:
        if doc["kind"] not in ("CustomResourceDefinition", "CompositeResourceDefinition"):
            raise SystemExit(f"{path}: unexpected kind {doc['kind']}")
        spec = doc["spec"]
        for v in spec["versions"]:
            write_schema(spec["group"], spec["names"]["kind"], v["name"],
                         v["schema"]["openAPIV3Schema"])


def main() -> None:
    for d in (BIN, SCHEMAS, SRC):
        d.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(SCHEMAS)
    SCHEMAS.mkdir()

    for sha, kind, url in read_lock():
        path = download(sha, url)
        if kind == "tool":
            with tarfile.open(path) as tar:
                for member in tar.getmembers():
                    if member.isfile() and "/" not in member.name:
                        tar.extract(member, BIN, filter="data")
                        (BIN / member.name).chmod(0o755)
                        print(f"tool {member.name}")
        elif kind == "crd":
            convert(path)
        elif kind.startswith("k8s:"):
            out = SCHEMAS / f"{kind[4:]}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, out)
            print(f"schema {kind[4:]}")
        else:
            raise SystemExit(f"unknown source type {kind}")

    # Our own API: the XRD in this repo becomes a schema for StorageBucket.
    for xrd in LOCAL_XRDS:
        convert(xrd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
