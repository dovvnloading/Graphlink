# Byte-faithful reviewed file previews

The reviewed Gitlink Apply path presents changes to UTF-8 file bytes, including
CRLF/LF/CR endings, the final newline, and a UTF-8 BOM. It refuses non-UTF-8
originals instead of guessing an encoding. The writer, selected-file checks,
proposal fingerprint, optimistic stale-file checks, and recovery protocol keep
their existing roles.

## Reproduced failure

On unmodified tracked source at
`72eec3cb7871a9b76f10bbd5fa85ed0356c27ae6`, harmless disposable file fixtures
passed through the real `prepare_change_review`, `_build_gitlink_preview_text`,
and `_call_gitlink_apply` functions, without replacing the validator or writer.
These requests all displayed `No textual diff available.` while Apply changed
bytes:

| Original bytes | Proposed bytes | Hidden change |
| --- | --- | --- |
| `alpha\r\n` | `alpha\n` | CRLF to LF |
| `alpha\n` | `alpha\r\n` | LF to CRLF |
| `alpha` | `alpha\n` | Final newline added |
| `alpha\n` | `alpha` | Final newline removed |
| `a\r\nb\n` | `a\nb\r\n` | Mixed endings reordered, same byte count |
| `caf\xe9\n` | UTF-8 `café\n` | Guessed cp1252 converted to UTF-8 |

An identical-byte fixture displayed the same message. Removing a UTF-8 BOM did
produce a diff, but the changed character was the invisible U+FEFF itself.
For the CRLF-to-LF fixture, the observed SHA-256 changed from
`4a31f8a329cdbbab944056fc992eeb5ab5c0b4a78c3bc34e6d7c4a59fd6cf3b6`
to `b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060`.
The probe used Python 3.12.14 on Linux 6.18.57 x86_64, glibc 2.41.

This minimal probe works on the reference and fixed source. It uses temporary
files only, no network, model, credentials, or Git mutation. Activate the
repository's locked Python environment first:

```sh
python - <<'PY'
import inspect
import tempfile
from pathlib import Path
from backend.agents import _build_gitlink_preview_text, _call_gitlink_apply
from graphlink_plugins.gitlink.change_review import prepare_change_review

for before, proposed in [
    (b"alpha\r\n", "alpha\n"),
    (b"alpha", "alpha\n"),
    (b"caf\xe9\n", "café\n"),
]:
    with tempfile.TemporaryDirectory(prefix="graphlink-preview-probe-") as root:
        target = Path(root) / "fixture.txt"
        target.write_bytes(before)
        try:
            changes, originals = prepare_change_review([
                {"path": target.name, "operation": "update", "content": proposed}
            ], root)
            keyword = ("original_bytes" if "original_bytes" in
                       inspect.signature(_build_gitlink_preview_text).parameters
                       else "original_texts")
            print(_build_gitlink_preview_text(
                changes, root, "local/fixture", "main", **{keyword: originals}))
            print("Applied:", _call_gitlink_apply(root, changes))
        except RuntimeError as error:
            print("Refused:", error)
        print("Before:", before.hex(), "After:", target.read_bytes().hex())
PY
```

On the fixed source, the first two cases disclose their byte and newline
changes and successfully write precisely the proposed bytes. The cp1252 case
is explicitly refused and the original bytes remain intact. A refusal here
is an encoding-support decision, not a claim that the original file is unsafe.

## Implementation and review boundary

`prepare_change_review` retains raw original bytes alongside the baseline
SHA-256. Preview construction consumes that exact capture without another
filesystem read. Proposed bytes are obtained with the same UTF-8 encoding
used by the reviewed writer. No Unicode or newline normalization occurs.

Each preview states whether bytes are identical, changed, created, or deleted;
records before/after sizes and SHA-256; reports CRLF/LF/CR counts, final-newline
state, and UTF-8 BOM presence; and displays a unified diff of escaped JSON
strings, one per physical line. Quotation marks expose leading/trailing spaces.
Literal `\r`, `\n`, `\t`, and Unicode escapes distinguish invisible content.
Empty-file creation and deletion are explicit existence changes even though
there is no content hunk. An unbound discovery preview says its original-byte
baseline is unavailable and requires a local checkout and regeneration.

The UI renders review text directly in `<pre><code>` text nodes. File content
cannot terminate a Markdown fence and add review headings, links, or images.
Unusual paths are displayed with visible escapes; selection and Apply still
send the exact original path. The `gitlinkPreviewText` wire field is unchanged.

New reviewed rows include server-held `review_format_version=1`, which is
covered by the existing full-proposal fingerprint. Reviewed Apply refuses
saved pre-upgrade rows even if their old hashes and fingerprint are internally
consistent: those previews must be regenerated. This check applies only to
`require_review=True`; the internal `apply_change_set` helper keeps its existing
`require_review=False` default. The version and fingerprint establish
consistency, not proof of human consent.

## Verification

Run from the repository root:

```sh
python -m pytest -q -n0 backend/tests/test_gitlink_byte_preview.py backend/tests/test_gitlink_review.py backend/tests/test_gitlink_selective.py backend/tests/test_gitlink_crash_recovery.py backend/tests/test_gitlink_safety_demo.py
python tools/gitlink_safety_demo.py --json gitlink-safety-report.json
```

Run from `web_ui`:

```sh
npm test -- --run src/app/canvas/GitlinkNodeView.test.tsx src/app/canvas/gitlinkPreview.test.ts
```

The byte-preview tests include real successful writes as positive controls,
UTF-8 and BOM changes, newline changes in both directions, mixed endings,
empty-file create/delete, identical bytes, whitespace, controls and Unicode,
non-UTF-8 refusal, malformed Unicode refusal, stale-file refusal, and legacy
review rejection through the production intent and dispatcher. Refusal cases
compare file hashes and use a wrapping observer around the real atomic writer
where relevant. Test-only fixtures replace model generation; the reviewed
validator and writer are not mocked. Git fixtures reuse the safety demo's
inherited-`GIT_*` scrubber.

Component tests verify text rendering, newline metadata, inert Markdown/HTML,
escaped paths, selection, cancellation, and stale confirmation behavior. They
do not replace browser-to-backend integration evidence. Run the repository's
full Python, frontend, Windows/POSIX, and Playwright gates on the final commit;
a targeted Linux pass is not a complete release result.

## Limits

Only UTF-8 (with or without a UTF-8 BOM) is supported by this reviewed preview.
UTF-16/32, cp1252, and other non-UTF-8 originals are refused without conversion.
Strictly valid UTF-8 bytes are presented reversibly; the code does not infer a
file's intended language or encoding from content. A human can still approve
incorrect or harmful content. These safeguards concern reviewed local file
writes, not arbitrary code execution, hostile local processes, multi-file
atomicity, all possible power-loss events, or external side effects. The
existing recovery limitations in `GITLINK_SAFETY_DEMO.md` still apply.
