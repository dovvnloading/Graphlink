"""Build-time only. Fetch pinned bytes, then compile and seal a small offline import closure."""

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/opt/graphlink")
LOCK_PATH = ROOT / "toolchain.lock.json"
LEAN = Path("/opt/lean")
SOURCES = Path("/opt/build/mathlib")
MODULES = Path("/opt/mathlib/lib/lean")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_output(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def checkout(url, revision, directory):
    # Each URL and revision comes from the locked, hash-checked mathlib tree.
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", url):
        raise ValueError("Expected a public GitHub source URL")
    if not re.fullmatch("[a-f0-9]{40}", revision):
        raise ValueError("Expected a full Git commit")
    directory.mkdir(parents=True)
    for args in [
        ["init", "--quiet"],
        ["remote", "add", "origin", url],
        ["-c", "fetch.fsckObjects=true", "fetch", "--no-tags", "--depth=1", "origin", revision],
        ["checkout", "--quiet", "--detach", "FETCH_HEAD"],
    ]:
        subprocess.run(["git", *args], cwd=directory, check=True)
    if checked_output("git", "rev-parse", "HEAD", cwd=directory) != revision:
        raise RuntimeError("Git checkout does not match the lock")


def fetch_lean(lock):
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        raise RuntimeError("This lock targets linux/amd64")
    release = lock["lean"]
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "lean.zip"
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--proto", "=https",
                        "--proto-redir", "=https", "--output", archive, release["archive"]], check=True)
        if digest(archive) != release["sha256"]:
            raise ValueError("Lean release archive failed SHA-256 verification")
        subprocess.run(["unzip", "-q", archive, "-d", tmp], check=True)
        (Path(tmp) / f"lean-{release['version']}-linux").rename(LEAN)


def fetch_mathlib(lock):
    release = lock["lean"]
    mathlib = lock["mathlib"]
    checkout(mathlib["url"], mathlib["revision"], SOURCES)
    expected_toolchain = "leanprover/lean4:v" + release["version"]
    if (SOURCES / "lean-toolchain").read_text().strip() != expected_toolchain:
        raise ValueError("Lean and mathlib toolchains disagree")
    manifest = SOURCES / "lake-manifest.json"
    if digest(manifest) != mathlib["manifest_sha256"]:
        raise ValueError("mathlib dependency manifest failed SHA-256 verification")
    for package in json.loads(manifest.read_bytes())["packages"]:
        if package["type"] != "git" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", package["name"]):
            raise ValueError("Unsupported dependency in locked manifest")
        checkout(package["url"], package["rev"], SOURCES / ".lake/packages" / package["name"])


def seal_modules():
    # Preserve modules' split olean parts, IR and native libraries together. Lake's source
    # and build configurations are never exposed on the final image's search path.
    roots = [SOURCES, *sorted((SOURCES / ".lake/packages").iterdir())]
    MODULES.mkdir(parents=True)
    for package in roots:
        source = package / ".lake/build/lib/lean"
        for item in sorted(source.rglob("*")):
            if item.is_dir() or item.name.endswith((".hash", ".trace", ".ilean")):
                continue
            if item.is_symlink():
                raise ValueError(f"Unexpected symlink in compiled modules: {item.name}")
            dest = MODULES / item.relative_to(source)
            if dest.exists():
                raise ValueError(f"Module collision in offline search path: {dest.name}")
            dest.parent.mkdir(exist_ok=True, parents=True)
            shutil.copyfile(item, dest)
    inventory = ROOT / "mathlib-modules.sha256"
    with inventory.open("w", encoding="utf-8", newline="\n") as out:
        for path in sorted(MODULES.rglob("*")):
            if path.is_file():
                out.write(f"{digest(path)}  {path.relative_to(MODULES).as_posix()}\n")
    return inventory


def build(lock):
    revision = checked_output(LEAN / "bin/lean", "--githash")
    if revision != lock["lean"]["revision"]:
        raise ValueError("Lean executable reports a different commit")
    module = lock["mathlib"]["module"]
    subprocess.run(["lake", "--no-cache", "build", "+" + module + ":olean"], cwd=SOURCES, check=True)
    inventory = seal_modules()
    if not (MODULES / (module.replace(".", "/") + ".olean")).is_file():
        raise RuntimeError("Requested mathlib module missing from sealed cache")
    evidence = {
        "schema": 1,
        "lock_sha256": digest(LOCK_PATH),
        "lean_revision": revision,
        "mathlib_revision": lock["mathlib"]["revision"],
        "modules_sha256": digest(inventory),
        "executables": {
            name: digest(LEAN / "bin" / name)
            for name in ("lean", "lake", "leanexport", "leanchecker", "nanoda_bin", "leanchecker-paranoid")
        },
    }
    (ROOT / "toolchain-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    action, = sys.argv[1:]
    {"fetch-lean": fetch_lean, "fetch-mathlib": fetch_mathlib, "build": build}[action](json.loads(LOCK_PATH.read_bytes()))
