# Formal Lean toolchain spike

Graphlink's first Lean milestone tests whether an independently replayed proof can be tied to an exact theorem in an offline container. It accepts six source-controlled fixtures. There is no Graphlink plugin, workspace input, or user-facing verification status in this milestone. The [implementation plan, §19](https://github.com/dovvnloading/Graphlink/blob/4523324e890aada048fe8b82a2c447704f7cc442/docs/FORMAL_LEAN_PLUGIN_PLAN.md#19-final-implementation-handoff) makes this evidence a prerequisite for product work.

## Reproduce the experiment

Run from the repository root on Linux x86-64 with Docker Engine, cgroup v2, runc, AppArmor and Docker's built-in seccomp profile. Provisioning fetches the locked inputs. Compilation of the mathlib import closure and all fixture stages run offline. No registry publication or runtime installation is necessary.

```sh
evidence=$(mktemp -d)
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 --progress=plain \
  --iidfile "$evidence/image-id.txt" -f tests/formal_lean_spike/image/Dockerfile \
  tests/formal_lean_spike
python3 tests/formal_lean_spike/run_spike.py \
  --image-id "$(cat "$evidence/image-id.txt")" --report "$evidence/results.json"
```

The workflow retains the Docker version, build transcript, local image ID and JSON measurements for 14 days. A failed measurement returns a nonzero status. Inspect the error and the last completed fixture in the report. A failed provisioning step leaves the build log. The stdlib-only host tests can also run without Docker.

```sh
python3 -m unittest discover -s tests/formal_lean_spike
```

## Toolchain and statement

| Input | P0 choice |
| --- | --- |
| Lean and bundled Lake | Lean v4.36.0, revision `611c117bfaf7b7ae1cfcf246ce6fba31e8f509e9`. The lock contains the Linux binary archive SHA-256. Each stage records both tool versions. |
| mathlib | Revision `fc4f59e577feb1a4956671bc04ac18aaaf8cd842`. Provisioning checks the dependency manifest digest, checks out each manifest revision and compiles the `Mathlib.Data.Nat.Basic` closure from source. |
| Image identity | The lock pins the Linux/amd64 base image manifest. Each build produces its own final local image configuration digest. The runner requires this build's full ID. A distributed release would also need a reviewed final registry manifest digest. |
| Trusted challenge | `checker/Challenge.lean` declares `Graphlink.Spike.addZero (n : Nat) : n + 0 = n`. The placeholder belongs to the challenge. The proof must come from the solution export. |
| Assumptions | The fixture policy allows zero axioms, including zero uses of classical choice, propositional extensionality or quotient soundness. The policy applies to the candidate's transitive dependencies. |
| Platform scope | Experimental Linux/amd64 only. Windows, macOS, ARM, Podman and production desktop checking are outside the measured profile. |

The image build exports the trusted challenge before any run. Each candidate compiles in its own fresh container. Only the bounded declaration export crosses into the replay container. The replay stage reads the challenge from the read-only image, compares the target, traverses exported declarations, and runs Lake's comparator with the Lean kernel and nanoda. Only the outer Docker container provides process isolation in this profile. The comparator's inner sandbox is disabled for export-to-export replay.

## Evidence to review

| Case | Required observation |
| --- | --- |
| Natural-number theorem, twice | Same named target, elaborated challenge type and export digests in both runs. Both kernels accept. No transitive axioms. |
| Changed proposition | A valid proof of `True` with the same theorem name is rejected for statement mismatch. |
| Direct and imported placeholders | Each candidate compiles. Independent replay attributes `sorryAx` to the target and rejects it. |
| Direct and imported custom axioms | Replay identifies the specific transitive assumption and records comparator rejection. |
| Isolation and cleanup | The runner checks daemon metadata and kernel-visible restrictions. A fixed waiting fixture must start, exceed its deadline, and be removed. |

The report binds the local image ID, toolchain and module inventory hashes, challenge and solution export hashes, elaborated type, assumptions, checker outcomes and timestamps. Review the command transcripts alongside the machine-readable measurements. Timestamps vary between runs. The report is unsigned experimental evidence. It grants no authority to a restored Graphlink session.

## Release disposition

Strict assurance is disabled. The fresh replay inspects dependencies directly from exported declarations. Coverage of the imported-inductive case in [Lean issue 15226](https://github.com/leanprover/lean4/issues/15226) and independent review of the completeness of this traversal remain release gates. A passing direct or imported-placeholder fixture alone cannot close that issue. Likewise, a working Ubuntu fixture suite does not establish desktop sandbox support, receipt persistence, undo or grant revocation. See the [acceptance matrix](https://github.com/dovvnloading/Graphlink/blob/4523324e890aada048fe8b82a2c447704f7cc442/docs/FORMAL_LEAN_PLUGIN_PLAN.md#17-delivery-acceptance-matrix-release-blockers).
