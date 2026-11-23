#!/usr/bin/env bash
# Build a single-platform image and preserve the actual ID of the loaded config.
set -euo pipefail

if [[ $# != 1 ]]; then
    echo 'Usage: build.sh EVIDENCE_DIRECTORY' >&2
    exit 2
fi
mkdir -p -- "$1"
evidence=$(cd -- "$1" && pwd)
image_dir=$(cd -- "$(dirname -- "$0")" && pwd)
context=$(dirname -- "$image_dir")
lock="$context/toolchain.lock.json"

read -r builder_image builder_config epoch < <(python3 - "$lock" <<'PY'
import json
import sys

with open(sys.argv[1], "rb") as f:
    settings = json.load(f)["build"]
sys.stdout.write(f"{settings['buildkit_image']} {settings['buildkit_config_digest']} {settings['source_date_epoch']}\n")
PY
)
# Names stay local to this run. The tag is retained so a second replay or a concurrent build is safe.
instance=$(python3 -c 'import uuid; import sys; sys.stdout.write(uuid.uuid4().hex)')
builder="graphlink-lean-$instance"
tag="graphlink-formal-lean-spike:$instance"
docker version > "$evidence/docker-version.txt"
docker buildx version > "$evidence/builder.txt"

docker buildx create --name "$builder" --driver docker-container --platform linux/amd64 \
    --driver-opt "image=$builder_image" >> "$evidence/builder.txt"
trap 'docker buildx rm "$builder" >&2' EXIT
docker buildx inspect "$builder" --bootstrap >> "$evidence/builder.txt"
actual_builder=$(docker image inspect --format '{{.Id}}' "$builder_image")
if [[ "$actual_builder" != "$builder_config" ]]; then
    echo "Local BuildKit config differs from the pinned linux/amd64 image" >&2
    exit 1
fi
printf 'builder-image=%s\nbuilder-config=%s\nsource-date-epoch=%s\n' \
    "$builder_image" "$actual_builder" "$epoch" >> "$evidence/builder.txt"

# The exporter streams its archive into Docker; storing another multi-GB tar is unnecessary.
# BuildKit's frontend is bundled with the pinned daemon. The epoch fixes config/history times
# and the exporter normalizes new layer entries, including files produced by COPY and RUN.
docker buildx build --builder "$builder" --platform linux/amd64 --progress=plain \
    --provenance=false --sbom=false --build-arg "SOURCE_DATE_EPOCH=$epoch" \
    --output type=docker,dest=-,rewrite-timestamp=true --tag "$tag" \
    --metadata-file "$evidence/build-metadata.json" -f "$image_dir/Dockerfile" "$context" \
    2> >(tee "$evidence/build.log" >&2) | docker image load

docker image inspect "$tag" > "$evidence/image-inspect.json"
docker image history --no-trunc --format '{{json .}}' "$tag" > "$evidence/image-history.jsonl"
# Buildx's manifest digest and the daemon's local config digest are different identifiers.
# Cross-check the export against the loaded config before using its immutable ID.
python3 - "$evidence" <<'PY'
import json
import re
import sys
from pathlib import Path

directory = Path(sys.argv[1])
metadata = json.loads((directory / "build-metadata.json").read_bytes())
image, = json.loads((directory / "image-inspect.json").read_bytes())
image_id = image["Id"]
if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) or metadata["containerimage.config.digest"] != image_id:
    sys.exit("Loaded Docker image config does not match the exported config digest")
if image["Architecture"] != "amd64" or image["Os"] != "linux" or not image["RootFS"]["Layers"]:
    sys.exit("Loaded image does not have the pinned platform and root filesystem")
(directory / "image-id.txt").write_text(image_id + "\n", encoding="ascii")
PY
printf '%s\n' "$tag" > "$evidence/image-tag.txt"
