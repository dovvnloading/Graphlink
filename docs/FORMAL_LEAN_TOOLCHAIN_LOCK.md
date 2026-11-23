# Formal Lean toolchain spike

Graphlink's first Lean milestone tests whether an independently replayed proof can be tied to an exact theorem in an offline container. It accepts seven source-controlled fixtures. Product integration follows review of the proof and isolation evidence. [Implementation plan, §19](https://github.com/dovvnloading/Graphlink/blob/4523324e890aada048fe8b82a2c447704f7cc442/docs/FORMAL_LEAN_PLUGIN_PLAN.md#19-final-implementation-handoff)

## Reproduce the experiment

Run from the repository root on Linux x86-64 with Docker Engine, cgroup v2, runc, AppArmor and Docker's built-in seccomp profile. Provisioning fetches the locked inputs. Compilation of the mathlib import closure and all fixture stages run offline. No registry publication or runtime installation is necessary.

```sh
evidence=$(mktemp -d)
bash tests/formal_lean_spike/image/build.sh "$evidence"
python3 tests/formal_lean_spike/run_spike.py \
  --image-id "$(cat "$evidence/image-id.txt")" --report "$evidence/results.json"
# After completing any repeat runs, release the local tag created for this build.
docker image rm "$(cat "$evidence/image-tag.txt")"
```

The build script uses Docker Buildx and a pinned BuildKit builder. The workflow builds on two fresh Ubuntu machines and compares the local image IDs, toolchains and complete replay measurements. It retains the Docker version, build transcript, local image identity and JSON measurements for 14 days. A failed measurement returns a nonzero status. Inspect the error and the last completed fixture in the report. A failed provisioning step leaves the build log. The stdlib-only host tests can also run without Docker.

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
| Assumptions | nanoda needs the pinned declaration of propositional extensionality (`propext`) to check the kernel primitive exports. The candidate theorem's separate policy allows zero transitive axioms, including zero uses of `propext`. Both appear separately in the report. |
| Platform scope | Experimental Linux/amd64 only. Windows, macOS, ARM, Podman and production desktop checking are outside the measured profile. |

The image build exports the trusted challenge before any run. Each candidate compiles in its own fresh container. Only the bounded declaration export crosses into the replay container. The replay stage reads the challenge from the read-only image, compares the target, traverses exported declarations, and runs Lake's comparator with the Lean kernel and nanoda. Only the outer Docker container provides process isolation in this profile. The comparator's inner sandbox is disabled for export-to-export replay.

## Evidence to review

| Case | Required observation |
| --- | --- |
| Natural-number theorem, twice | Same named target, elaborated challenge type and export digests in both runs. Both kernels accept. No transitive axioms. |
| Changed proposition | A valid proof of `True` with the same theorem name is rejected for statement mismatch. |
| Direct and imported placeholders | Each candidate compiles. Independent replay attributes `sorryAx` to the target and rejects it. |
| Direct and imported custom axioms | Replay identifies the specific transitive assumption and records comparator rejection. |
| Proof using `propext` | Both kernels accept the proof under the replay environment's assumptions. The inspector attributes `propext` to the theorem and the zero-axiom target policy rejects it. |
| Isolation and cleanup | The runner checks daemon metadata and kernel-visible restrictions. A fixed waiting fixture must start, exceed its deadline, and be removed. |

The report binds the local image ID, toolchain and module inventory hashes, challenge and solution export hashes, elaborated type, assumptions, checker outcomes and timestamps. Review the command transcripts alongside the machine-readable measurements. Timestamps vary between runs. The report is unsigned experimental evidence. It grants no authority to a restored Graphlink session.

## Observed run on November 23, 2026

On Ubuntu 24.04 x86-64 with Docker Engine 28.0.4 and runc 1.5.3, all seven fixture cases produced their expected outcomes. The eighth measurement repeated the positive case in two fresh containers. Both kernels accepted the same proof export on both attempts. The timeout fixture started and its container was removed. The [unaltered JSON transcript](evidence/formal_lean_p0_run_45179416012.json) preserves all eight outcomes, commands, assumptions and runtime measurements. [GitHub Actions run 45179416012](https://github.com/dovvnloading/Graphlink/actions/runs/45179416012)

| Measured input or output | Identity |
| --- | --- |
| Tested source commit | `287ebeaed10b61de23727579d1413cc0c8494ce5` |
| Final local Docker image ID | `sha256:e301ca4a7de945a8f119044cc042ade43adaf99fcaaa164ebebe8b4b98214661` |
| Lake version | `5.0.0-src+611c117` |
| Compiled module inventory SHA-256 | `32a109eadfd951f238afd4d120181df24ecef9282f9c01ebada69efddb405bdb` |
| Immutable challenge export SHA-256 | `f688b6cb011c35ffbebbc2db980332a930b430ca59bfb50af267e8913108c3d9` |
| Transcript SHA-256 | `578a4b2a39e12aa9a8706c86168de749dd002633512669b4a6e8101c7460b0da` |

The earlier positive run stopped when nanoda could not resolve `propext` while replaying primitive declarations. The final experiment supplies that declaration to the replay environment. The additional `target_propext` fixture records kernel acceptance and target-policy rejection together. [Earlier run 45178525906](https://github.com/dovvnloading/Graphlink/actions/runs/45178525906)

## Release disposition

Strict assurance is disabled. The inspector follows references in exported expressions, declaration types and bodies, mutual inductive families, constructors, and recursor rules. Its own two compile-time checks exercise mutual-family and recursor records. Coverage of the specific imported-inductive case and independent review of the completeness of this traversal remain release gates. [Lean issue 15226](https://github.com/leanprover/lean4/issues/15226)

The live transcript covers the fixed fixtures and the Ubuntu profile. Product gates for desktop isolation, editor behavior, receipt persistence, undo and grant revocation remain open. [Acceptance matrix](https://github.com/dovvnloading/Graphlink/blob/4523324e890aada048fe8b82a2c447704f7cc442/docs/FORMAL_LEAN_PLUGIN_PLAN.md#17-delivery-acceptance-matrix-release-blockers)
