#!/usr/bin/env python3
# Copyright 2026 Adnero.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regenerates ads_mcp/schemas/<API_VERSION>/ from the googleapis Google Ads protos at a pinned commit.

Why the .proto files: the installed google-ads package (proto-plus) carries no `google.api.field_behavior`
options (checked: every FieldOptions in its v26 descriptors is empty), so REQUIRED / IMMUTABLE / OUTPUT_ONLY
exist only in googleapis.

    pip install grpcio-tools            # protoc, build time only
    python scripts/gen_schemas.py       # fetch (git sparse, ~10 MB), compile, write
    python scripts/gen_schemas.py --check   # exit 1 when the checked-in data differs

Moving to a new API version: set GOOGLEAPIS_COMMIT to a googleapis commit with that version's protos, change
ads_mcp/api_version.py, run this, commit both.
"""

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ads_mcp.api_version import API_VERSION  # noqa: E402
from ads_mcp.schema import proto2js  # noqa: E402

# googleapis "feat(googleads): Protos and build files for Google Ads API v26" (2026-11-11, v26.2), the protos of
# google-ads 35.1.0.
GOOGLEAPIS_COMMIT = "8f28f48bf9ea3ec0f14a75a5cfc970819cec9e2b"
GOOGLEAPIS_REPO = "https://github.com/googleapis/googleapis.git"
FILES = ("resources", "common", "services", "enums", "index")
SPARSE = (
    "google/ads/googleads/{v}/",
    "google/api/",
    "google/rpc/",
    "google/longrunning/",
    "google/type/",
)


def encode(data: dict) -> bytes:
    """Canonical JSON (sorted keys, no spaces, UTF-8): the bytes we hash and compare."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def fetch(cache: pathlib.Path, commit: str, version: str) -> pathlib.Path:
    """A sparse checkout of googleapis at `commit` (only the protos we compile), reused when present."""
    target = cache / commit
    if (target / "google/ads/googleads" / version).is_dir():
        return target
    target.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=target, check=True)
    run("init", "-q")
    run("remote", "add", "origin", GOOGLEAPIS_REPO)
    run(
        "sparse-checkout",
        "set",
        "--no-cone",
        *(p.format(v=version) for p in SPARSE),
    )
    run("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)
    run("checkout", "-q", "FETCH_HEAD")
    return target


def compile_protos(googleapis: pathlib.Path, version: str):
    """protoc (grpc_tools) -> FileDescriptorSet with comments, for resources/common/services/enums."""
    import grpc_tools
    from google.protobuf import descriptor_pb2

    include = pathlib.Path(grpc_tools.__file__).parent / "_proto"
    base = googleapis / "google/ads/googleads" / version
    protos = sorted(
        str(p.relative_to(googleapis))
        for kind in ("resources", "common", "services", "enums")
        for p in (base / kind).glob("*.proto")
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "set.pb"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"-I{googleapis}",
                f"-I{include}",
                "--include_source_info",
                "--include_imports",
                f"--descriptor_set_out={out}",
                *protos,
            ],
            cwd=googleapis,
            check=True,
            capture_output=True,
        )
        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(out.read_bytes())
    return fds


def generate(googleapis: pathlib.Path, version: str, commit: str) -> dict:
    data = proto2js.convert(compile_protos(googleapis, version), version)
    data["index"]["googleapis_commit"] = commit
    return data


def write(out_dir: pathlib.Path, data: dict) -> dict:
    """<kind>.json.gz per kind (gzip mtime 0) + manifest.json (sha256 and sizes of the JSON). Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in FILES:
        raw = encode(data[name])
        packed = gzip.compress(raw, compresslevel=9, mtime=0)
        (out_dir / f"{name}.json.gz").write_bytes(packed)
        manifest[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "json_bytes": len(raw),
            "gzip_bytes": len(packed),
            "entries": (
                len(data[name])
                if name != "index"
                else len(data[name]["resources"])
            ),
        }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    )
    return manifest


def differences(out_dir: pathlib.Path, data: dict) -> list[str]:
    """Kinds whose checked-in JSON differs from `data` (compared uncompressed: zlib builds may pack differently)."""

    def current(name):
        path = out_dir / f"{name}.json.gz"
        return gzip.decompress(path.read_bytes()) if path.exists() else b""

    return [n for n in FILES if current(n) != encode(data[n])]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--googleapis",
        help="An existing googleapis checkout (else fetched at the pinned commit).",
    )
    parser.add_argument(
        "--cache", default=os.path.expanduser("~/.cache/gads-mcp-googleapis")
    )
    parser.add_argument(
        "--out", default=str(ROOT / "ads_mcp" / "schemas" / API_VERSION)
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare only; exit 1 on any difference.",
    )
    args = parser.parse_args(argv)
    googleapis = (
        pathlib.Path(args.googleapis)
        if args.googleapis
        else fetch(pathlib.Path(args.cache), GOOGLEAPIS_COMMIT, API_VERSION)
    )
    data = generate(googleapis, API_VERSION, GOOGLEAPIS_COMMIT)
    out = pathlib.Path(args.out)
    if args.check:
        diff = differences(out, data)
        print("up to date" if not diff else f"differs: {', '.join(diff)}")
        return 1 if diff else 0
    manifest = write(out, data)
    total = sum(m["gzip_bytes"] for m in manifest.values())
    for name, m in manifest.items():
        print(
            f"{name:10} {m['entries']:5} entries  {m['json_bytes']:>9,} B json  {m['gzip_bytes']:>8,} B gzip"
        )
    print(f"total gzip {total:,} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
