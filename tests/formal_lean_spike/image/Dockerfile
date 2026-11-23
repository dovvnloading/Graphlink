# The linux/amd64 child manifest of python:3.12-bookworm supplies Python, git and unzip.
FROM docker.io/library/python@sha256:3c3ba8ad4c85eb63b7568f806434f14e33370056985da7fdf2673621cb1d7e93 AS base
ENV PATH="/opt/lean/bin:${PATH}"

FROM base AS toolchain
COPY toolchain.lock.json /opt/graphlink/toolchain.lock.json
# Share a single complete official distribution layer between compilation and runtime.
RUN --mount=type=bind,source=image/provision.py,target=/tmp/provision.py \
    python3 -I /tmp/provision.py fetch-lean

FROM toolchain AS build
COPY image/provision.py /opt/build/provision.py
RUN python3 -I /opt/build/provision.py fetch-mathlib
# All Lean evaluation, including the trusted mathlib build, happens offline.
RUN --network=none python3 -I /opt/build/provision.py build

FROM toolchain AS runner
COPY --from=build /opt/mathlib /opt/mathlib
COPY --from=build /opt/graphlink/mathlib-modules.sha256 /opt/graphlink/toolchain-evidence.json /opt/graphlink/
COPY checker/ /opt/graphlink/checker/
COPY isolation_entrypoint.py /opt/graphlink/isolation_entrypoint.py
ENV LEAN_PATH=/opt/mathlib/lib/lean LANG=C.UTF-8 HOME=/tmp
WORKDIR /tmp
RUN --network=none python3 -I /opt/graphlink/checker/stage.py prepare
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/python3", "-I", "/opt/graphlink/isolation_entrypoint.py"]
