"""Deterministic scoring/rubric engine for the Code Review Agent plugin.

Extracted from graphite_plugin_code_review.py. This module has zero Qt
dependencies, so it's directly unit-testable without constructing any widget.

Deliberately NOT merged with QualityGateAnalyzer's equivalent engine in
graphite_plugin_quality_gate.py, even though they share the same JSON-parsing
skeleton (already factored out into graphite_plugins/common/llm_json.py) - each
rubric's actual scoring weights, categories, and fallback heuristics are genuinely
different domain logic, not incidental duplication. See
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.2 for why unifying the rubrics
themselves (as opposed to the boilerplate around them) is out of scope here.
"""

import ast
import re

import graphite_config as config
from graphite_plugins.common.llm_json import call_llm_and_parse_json, extract_json_object


REVIEW_CATEGORY_WEIGHTS = {
    "correctness": 24,
    "reliability": 16,
    "security": 14,
    "maintainability": 14,
    "readability": 10,
    "testing": 10,
    "performance": 6,
    "architecture": 6,
}

REVIEW_CATEGORY_LABELS = {
    "correctness": "Correctness",
    "reliability": "Reliability",
    "security": "Security",
    "maintainability": "Maintainability",
    "readability": "Readability",
    "testing": "Testing",
    "performance": "Performance",
    "architecture": "Architecture",
}

SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

TEXT_FILE_EXCLUSION_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".wav", ".ogg", ".mp4", ".mov",
    ".avi", ".webm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".exe", ".dll",
    ".so", ".dylib", ".class", ".jar", ".pyc", ".pyd", ".bin", ".dat", ".db",
)

CODE_REVIEW_METRIC_MARKDOWN = """## Deterministic Review Metric

This plugin uses a fixed, repeatable rubric before the model is allowed to grade the file.

### Preflight Gate

1. Confirm the source is present, readable, and large enough to review.
2. Identify the file's likely language, runtime, and execution boundary.
3. Note whether the review sees the full file or a truncated excerpt.
4. Identify external assumptions: imports, environment variables, network calls, filesystem access, framework hooks.
5. Decide whether there is enough evidence to score each category fairly. If not, mark the gap instead of guessing.

### Required Inspection Sequence

1. Trace the happy-path control flow from input to output.
2. Check edge cases, null/empty states, and failure branches.
3. Inspect error handling, retries, cleanup, and state consistency.
4. Inspect secrets, auth, injection risk, unsafe execution, and trust boundaries.
5. Inspect data contracts, side effects, and dependency assumptions.
6. Inspect readability, cohesion, naming, duplication, and complexity.
7. Inspect tests, observability, and how the code could be validated.
8. Inspect performance hotspots only where the visible code suggests a real risk.
9. Separate high-confidence errors from lower-confidence review findings.
10. Produce scores from the fixed weights below instead of ad hoc scoring.

### Weighted Scorecard

- Correctness: 24%
- Reliability: 16%
- Security: 14%
- Maintainability: 14%
- Readability: 10%
- Testing: 10%
- Performance: 6%
- Architecture: 6%

### Verdict Gates

- `Strong`: weighted score >= 78, no critical errors, no high-severity findings.
- `Needs Revision`: weighted score 60-77, or at least one high-confidence error, or at least one high-severity finding.
- `Not Ready`: weighted score < 60, or at least one critical error.

### Output Contract

- Overview: short executive review of what matters most.
- Review Findings: evidence-backed issues ordered by severity.
- Errors Found: only high-confidence bugs / faults / security defects.
- Code Quality Report: deterministic weighted score plus release risk.
"""


def _clean_text(value, limit=None):
    text = str(value or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _clamp_score(value, default=70):
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = default
    return max(0, min(100, numeric))


def _severity_key(value):
    severity = _clean_text(value, limit=20).lower()
    return severity if severity in SEVERITY_ORDER else "medium"


def _titleize_key(value):
    cleaned = re.sub(r"[_-]+", " ", _clean_text(value, limit=80)).strip()
    return cleaned.title() if cleaned else "General"


def _looks_like_python(source_state, source_text):
    path = (
        source_state.get("path")
        or source_state.get("local_path")
        or source_state.get("label")
        or ""
    ).lower()
    if path.endswith(".py"):
        return True
    tokens = ("def ", "class ", "import ", "from ", "async def ")
    return sum(1 for token in tokens if token in source_text) >= 2


def _decode_text_bytes(raw_bytes):
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _prepare_numbered_source(source_text, max_chars=40000):
    lines = source_text.splitlines() or [source_text]
    total_lines = len(lines)
    visible_lines = []
    current_length = 0

    for index, line in enumerate(lines, start=1):
        numbered_line = f"{index:04d}: {line}"
        projected = current_length + len(numbered_line) + 1
        if visible_lines and projected > max_chars:
            break
        visible_lines.append(numbered_line)
        current_length = projected

    truncated = len(visible_lines) < total_lines
    return "\n".join(visible_lines), truncated, total_lines, len(visible_lines)


def _is_reviewable_repo_path(path_text):
    lowered = path_text.lower()
    return not lowered.endswith(TEXT_FILE_EXCLUSION_SUFFIXES)


def _source_origin_label(source_state):
    origin = source_state.get("origin", "")
    if origin == "github":
        repo = source_state.get("repo", "")
        branch = source_state.get("branch", "")
        file_path = source_state.get("path", "")
        parts = [part for part in (repo, branch, file_path) if part]
        return f"GitHub: {' / '.join(parts)}" if parts else "GitHub file"
    if origin == "local":
        return f"Local File: {source_state.get('local_path', '') or source_state.get('label', 'Loaded file')}"
    if origin == "manual":
        return "Manual / pasted source"
    return "No source selected"


def _compact_label_text(text, limit=34):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _source_scope_summary(payload):
    source_state = payload.get("source_state", {})
    context_text = _clean_text(payload.get("review_context", ""), limit=400)
    numbered_source = payload.get("source_for_model", "")
    summary_lines = [
        f"- Source: {_source_origin_label(source_state)}",
        f"- Total lines loaded: {payload.get('total_lines', 0)}",
        f"- Visible lines reviewed by the model: {payload.get('visible_lines', 0)}",
        f"- Full file visible to model: {'No' if payload.get('source_truncated') else 'Yes'}",
    ]
    if context_text:
        summary_lines.append(f"- Review context: {context_text}")
    if source_state.get("edited"):
        summary_lines.append("- Loaded source was manually edited inside the plugin before review.")
    if not numbered_source.strip():
        summary_lines.append("- Source excerpt: unavailable")
    return "\n".join(summary_lines)


class CodeReviewAnalyzer:
    SYSTEM_PROMPT = f"""
You are Graphlink's Code Review Agent.

Your job is to produce a disciplined, repeatable single-file code review.
You must use the exact checklist and weighted scoring model below instead of inventing a new rubric each time.

{CODE_REVIEW_METRIC_MARKDOWN}

Rules:
1. Be evidence-driven. Do not invent dependencies, tests, runtime behavior, or unseen files.
2. Separate high-confidence errors from broader review findings.
3. High-confidence errors must be concrete faults such as syntax problems, likely runtime failures, security defects, or clearly broken logic.
4. Review findings can include maintainability, readability, testing, or architectural concerns, but still require visible evidence.
5. If the source is truncated, only review what is visible and explicitly mention the visibility limit.
6. Avoid low-value stylistic nitpicks unless they materially affect readability, safety, maintainability, or correctness.
7. Output valid JSON only. No markdown fences, no commentary outside the JSON object.

Return exactly this shape:
{{
  "title": "Short review title",
  "overview": "2-4 sentence executive summary",
  "confidence": "high",
  "preflight_checks": [
    {{
      "check": "Source completeness",
      "status": "pass",
      "details": "What was verified before scoring"
    }}
  ],
  "review_findings": [
    {{
      "severity": "medium",
      "category": "maintainability",
      "title": "Short finding title",
      "evidence": "Visible code evidence only",
      "impact": "Why this matters",
      "recommendation": "Concrete improvement"
    }}
  ],
  "errors_found": [
    {{
      "severity": "high",
      "kind": "runtime",
      "title": "Short error title",
      "evidence": "Visible code evidence only",
      "fix": "Concrete remediation"
    }}
  ],
  "category_scores": {{
    "correctness": 80,
    "reliability": 78,
    "security": 86,
    "maintainability": 74,
    "readability": 81,
    "testing": 62,
    "performance": 76,
    "architecture": 73
  }},
  "quality_summary": "Short synthesis that aligns with the findings and scores"
}}
"""

    def _extract_json(self, raw_text):
        # Delegates to the shared regex (doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section
        # 1.6/3.5) - kept as a wrapper so this method's existing call site is unchanged.
        return extract_json_object(raw_text)

    def _normalize_preflight(self, payload, checks):
        default_checks = [
            {
                "check": "Source loaded",
                "status": "pass" if payload.get("source_text", "").strip() else "fail",
                "details": "The plugin has source text to inspect." if payload.get("source_text", "").strip() else "No source text was supplied.",
            },
            {
                "check": "Language/runtime identified",
                "status": "pass" if payload.get("source_state", {}).get("label") or payload.get("source_text", "").strip() else "warn",
                "details": "The reviewer can infer likely runtime context from the file path or visible syntax.",
            },
            {
                "check": "Visibility limit assessed",
                "status": "warn" if payload.get("source_truncated") else "pass",
                "details": "The review only sees a truncated excerpt." if payload.get("source_truncated") else "The full file is available to the reviewer.",
            },
            {
                "check": "External assumptions noted",
                "status": "pass",
                "details": "Imports, filesystem access, network calls, and framework assumptions must be treated as explicit review surface.",
            },
            {
                "check": "Scoring evidence threshold",
                "status": "pass" if payload.get("visible_lines", 0) >= 5 else "warn",
                "details": "There is enough visible source to score the file with reasonable confidence." if payload.get("visible_lines", 0) >= 5 else "The file is sparse, so scoring confidence is lower.",
            },
        ]

        normalized = []
        for item in checks or []:
            if not isinstance(item, dict):
                continue
            status = _clean_text(item.get("status"), limit=20).lower()
            if status not in {"pass", "warn", "fail"}:
                status = "warn"
            normalized.append({
                "check": _clean_text(item.get("check"), limit=120) or "Unnamed preflight check",
                "status": status,
                "details": _clean_text(item.get("details"), limit=280) or "No details supplied.",
            })

        if len(normalized) < 5:
            for default_item in default_checks:
                if not any(existing["check"] == default_item["check"] for existing in normalized):
                    normalized.append(default_item)
        return normalized[:8]

    def _normalize_findings(self, findings, is_error_list=False):
        normalized = []
        for item in findings or []:
            if not isinstance(item, dict):
                continue
            severity = _severity_key(item.get("severity"))
            title = _clean_text(item.get("title"), limit=120)
            evidence = _clean_text(item.get("evidence"), limit=420)
            if not title or not evidence:
                continue

            normalized_item = {
                "severity": severity,
                "title": title,
                "evidence": evidence,
            }

            if is_error_list:
                normalized_item["kind"] = _titleize_key(item.get("kind") or item.get("category") or "runtime")
                normalized_item["fix"] = _clean_text(item.get("fix"), limit=320) or "Address the visible root cause and re-run validation."
            else:
                normalized_item["category"] = _titleize_key(item.get("category") or "general")
                normalized_item["impact"] = _clean_text(item.get("impact"), limit=320) or "This issue reduces confidence in the file's quality or safety."
                normalized_item["recommendation"] = _clean_text(item.get("recommendation"), limit=320) or "Tighten the implementation and add verification for this path."

            normalized.append(normalized_item)

        normalized.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 5), item["title"]))
        return normalized[:10]

    def _normalize_scores(self, parsed_scores):
        scores = {}
        for key in REVIEW_CATEGORY_WEIGHTS:
            scores[key] = _clamp_score((parsed_scores or {}).get(key), default=72)
        return scores

    def _compute_weighted_score(self, category_scores):
        weighted_total = 0.0
        for key, weight in REVIEW_CATEGORY_WEIGHTS.items():
            weighted_total += category_scores[key] * (weight / 100.0)
        return int(round(weighted_total))

    def _derive_verdict(self, overall_score, findings, errors):
        critical_errors = sum(1 for item in errors if item["severity"] == "critical")
        high_errors = sum(1 for item in errors if item["severity"] == "high")
        high_findings = sum(1 for item in findings if item["severity"] in {"critical", "high"})

        if critical_errors > 0 or overall_score < 60:
            verdict = "not_ready"
        elif high_errors > 0 or high_findings > 0 or overall_score < 78:
            verdict = "needs_revision"
        else:
            verdict = "strong"

        if critical_errors > 0 or overall_score < 60:
            risk = "high"
        elif high_errors > 0 or overall_score < 78:
            risk = "medium"
        else:
            risk = "low"
        return verdict, risk

    def _build_overview_markdown(self, normalized, payload):
        preflight_lines = []
        for item in normalized["preflight_checks"]:
            status = item["status"].upper()
            preflight_lines.append(f"- `{status}` {item['check']}: {item['details']}")

        return "\n".join([
            "## Review Overview",
            "",
            normalized["overview"],
            "",
            "### Review Scope",
            _source_scope_summary(payload),
            "",
            "### Preflight Checklist",
            *preflight_lines,
        ])

    def _build_findings_markdown(self, normalized):
        findings = normalized["review_findings"]
        if not findings:
            return "\n".join([
                "## Review Findings",
                "",
                "No additional evidence-backed review findings were identified beyond the high-confidence errors list.",
            ])

        lines = ["## Review Findings", ""]
        for index, finding in enumerate(findings, start=1):
            lines.extend([
                f"### {index}. [{finding['severity'].upper()}] {finding['title']}",
                f"- Category: {finding['category']}",
                f"- Evidence: {finding['evidence']}",
                f"- Impact: {finding['impact']}",
                f"- Recommendation: {finding['recommendation']}",
                "",
            ])
        return "\n".join(lines).rstrip()

    def _build_errors_markdown(self, normalized):
        errors = normalized["errors_found"]
        if not errors:
            return "\n".join([
                "## Errors Found",
                "",
                "No high-confidence errors were identified from the visible source.",
            ])

        lines = ["## Errors Found", ""]
        for index, error in enumerate(errors, start=1):
            lines.extend([
                f"### {index}. [{error['severity'].upper()}] {error['title']}",
                f"- Kind: {error['kind']}",
                f"- Evidence: {error['evidence']}",
                f"- Fix: {error['fix']}",
                "",
            ])
        return "\n".join(lines).rstrip()

    def _build_quality_markdown(self, normalized):
        score_lines = []
        for key in REVIEW_CATEGORY_WEIGHTS:
            score_lines.append(
                f"- {REVIEW_CATEGORY_LABELS[key]} ({REVIEW_CATEGORY_WEIGHTS[key]}%): {normalized['category_scores'][key]}/100"
            )

        verdict_label = normalized["verdict"].replace("_", " ").title()
        confidence_label = normalized["confidence"].title()
        risk_label = normalized["risk_level"].title()

        return "\n".join([
            "## Code Quality Report",
            "",
            f"- Deterministic weighted score: {normalized['quality_score']}/100",
            f"- Verdict: {verdict_label}",
            f"- Confidence: {confidence_label}",
            f"- Release risk: {risk_label}",
            "",
            "### Weighted Scorecard",
            *score_lines,
            "",
            "### Summary",
            normalized["quality_summary"],
            "",
            "### Verdict Logic",
            "- `Strong`: score >= 78, no critical errors, no high-severity findings.",
            "- `Needs Revision`: score 60-77, or any high-confidence error, or any high-severity finding.",
            "- `Not Ready`: score < 60, or any critical error.",
        ])

    def _build_combined_markdown(self, overview_markdown, findings_markdown, errors_markdown, quality_markdown):
        return "\n\n".join([
            overview_markdown,
            findings_markdown,
            errors_markdown,
            quality_markdown,
        ])

    def _build_quality_summary(self, normalized):
        findings_count = len(normalized["review_findings"])
        errors_count = len(normalized["errors_found"])
        strongest_category = max(normalized["category_scores"], key=lambda key: normalized["category_scores"][key])
        weakest_category = min(normalized["category_scores"], key=lambda key: normalized["category_scores"][key])

        summary = _clean_text(normalized.get("quality_summary"), limit=420)
        if summary:
            return summary

        return (
            f"The file scores strongest in {REVIEW_CATEGORY_LABELS[strongest_category].lower()} "
            f"and weakest in {REVIEW_CATEGORY_LABELS[weakest_category].lower()}. "
            f"The review surfaced {findings_count} broader findings and {errors_count} high-confidence errors."
        )

    def _fallback_review(self, payload, exception_text=None):
        source_text = payload.get("source_text", "")
        source_state = payload.get("source_state", {})
        findings = []
        errors = []
        scores = {key: 82 for key in REVIEW_CATEGORY_WEIGHTS}

        def add_finding(severity, category, title, evidence, impact, recommendation):
            findings.append({
                "severity": severity,
                "category": category,
                "title": title,
                "evidence": evidence,
                "impact": impact,
                "recommendation": recommendation,
            })

        def add_error(severity, kind, title, evidence, fix):
            errors.append({
                "severity": severity,
                "kind": kind,
                "title": title,
                "evidence": evidence,
                "fix": fix,
            })

        if _looks_like_python(source_state, source_text):
            try:
                ast.parse(source_text)
            except SyntaxError as exc:
                evidence = f"Python parser raised a syntax error near line {exc.lineno}: {exc.msg}."
                add_error(
                    "critical",
                    "Syntax",
                    "Python syntax error prevents execution",
                    evidence,
                    "Fix the syntax error before running or reviewing downstream behavior.",
                )
                scores["correctness"] = min(scores["correctness"], 25)
                scores["reliability"] = min(scores["reliability"], 30)
                scores["maintainability"] = min(scores["maintainability"], 38)

        if re.search(r"(api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]+['\"]", source_text, re.IGNORECASE):
            add_error(
                "high",
                "Security",
                "Hard-coded secret-like value detected",
                "The file appears to assign a literal value to a secret-like variable name.",
                "Move the value to secure configuration or environment-based secret management.",
            )
            scores["security"] = min(scores["security"], 35)
            scores["maintainability"] = min(scores["maintainability"], 55)

        if re.search(r"\b(eval|exec)\s*\(", source_text):
            add_finding(
                "high",
                "security",
                "Dynamic code execution increases risk",
                "The file calls `eval(...)` or `exec(...)` directly.",
                "Dynamic execution expands injection and debugging risk.",
                "Replace dynamic execution with explicit parsing or a constrained execution strategy.",
            )
            scores["security"] = min(scores["security"], 40)

        if re.search(r"subprocess\.(Popen|run)\(.*shell\s*=\s*True", source_text, re.IGNORECASE | re.DOTALL) or "os.system(" in source_text:
            add_finding(
                "high",
                "security",
                "Shell execution path requires strict input control",
                "The file invokes a shell command path from code.",
                "Shell execution becomes dangerous if any untrusted input reaches the command.",
                "Prefer argument lists, validate inputs, and avoid shell invocation when possible.",
            )
            scores["security"] = min(scores["security"], 45)

        if re.search(r"except\s*:\s*\n", source_text):
            add_finding(
                "medium",
                "reliability",
                "Bare exception handler hides root causes",
                "The file contains a bare `except:` block.",
                "Bare exception handling can swallow unrelated failures and make debugging harder.",
                "Catch only expected exception types and log or re-raise unexpected ones.",
            )
            scores["reliability"] = min(scores["reliability"], 60)

        if re.search(r"except\s+Exception\s*:\s*pass", source_text):
            add_error(
                "high",
                "Reliability",
                "Exception is silently discarded",
                "The file uses `except Exception: pass`, which hides execution failures.",
                "Handle the exception explicitly or surface the failure so the caller can react.",
            )
            scores["reliability"] = min(scores["reliability"], 42)

        if re.search(r"\b(TODO|FIXME)\b", source_text):
            add_finding(
                "low",
                "maintainability",
                "Outstanding TODO or FIXME markers remain in the file",
                "The visible source still contains TODO/FIXME markers.",
                "Open TODO markers often indicate unfinished edge cases or deferred cleanup.",
                "Either resolve the pending work or convert the note into a tracked issue with clear ownership.",
            )
            scores["maintainability"] = min(scores["maintainability"], 72)

        if re.search(r"\b(print|console\.log)\s*\(", source_text):
            add_finding(
                "low",
                "readability",
                "Debug logging remains in the file",
                "The visible source includes raw debug logging calls.",
                "Ad hoc logging can add noise and make production behavior harder to reason about.",
                "Replace debug prints with structured logging or remove them before release.",
            )
            scores["readability"] = min(scores["readability"], 74)

        long_line_count = sum(1 for line in source_text.splitlines() if len(line) > 140)
        if long_line_count >= 5:
            add_finding(
                "low",
                "readability",
                "Several lines exceed a maintainable width",
                f"The file contains {long_line_count} lines longer than 140 characters.",
                "Very long lines usually hide complexity and make review and debugging slower.",
                "Break long expressions into named steps or helper functions.",
            )
            scores["readability"] = min(scores["readability"], 70)

        if payload.get("source_truncated"):
            scores["architecture"] = min(scores["architecture"], 74)
            scores["testing"] = min(scores["testing"], 74)

        if not findings and not errors:
            overview = "The visible file is structurally clean in this heuristic pass, with no immediately obvious high-confidence defects. A full model-driven review should still be preferred for architectural nuance and testability judgment."
        else:
            overview = "The fallback review identified concrete issues in the visible source. The most important next step is to address the highest-severity items before relying on the file in a production path."

        if exception_text:
            overview += f" A heuristic fallback was used because the model review could not be completed cleanly: {_clean_text(exception_text, limit=120)}."

        return {
            "title": "Code Review",
            "overview": overview,
            "confidence": "low" if exception_text else "medium",
            "preflight_checks": self._normalize_preflight(payload, []),
            "review_findings": findings,
            "errors_found": errors,
            "category_scores": scores,
            "quality_summary": "",
        }

    def _normalize_response(self, parsed, payload):
        if not isinstance(parsed, dict):
            parsed = {}

        normalized = {
            "title": _clean_text(parsed.get("title"), limit=120) or "Code Review",
            "overview": _clean_text(parsed.get("overview"), limit=600) or "The file was reviewed against a fixed engineering quality rubric.",
            "confidence": _clean_text(parsed.get("confidence"), limit=20).lower() or "medium",
            "preflight_checks": self._normalize_preflight(payload, parsed.get("preflight_checks", [])),
            "review_findings": self._normalize_findings(parsed.get("review_findings", [])),
            "errors_found": self._normalize_findings(parsed.get("errors_found", []), is_error_list=True),
            "category_scores": self._normalize_scores(parsed.get("category_scores", {})),
            "quality_summary": _clean_text(parsed.get("quality_summary"), limit=420),
        }

        if normalized["confidence"] not in {"low", "medium", "high"}:
            normalized["confidence"] = "medium"

        normalized["quality_score"] = self._compute_weighted_score(normalized["category_scores"])
        normalized["verdict"], normalized["risk_level"] = self._derive_verdict(
            normalized["quality_score"],
            normalized["review_findings"],
            normalized["errors_found"],
        )
        normalized["quality_summary"] = self._build_quality_summary(normalized)
        normalized["finding_count"] = len(normalized["review_findings"])
        normalized["error_count"] = len(normalized["errors_found"])
        normalized["metric_markdown"] = CODE_REVIEW_METRIC_MARKDOWN
        normalized["overview_markdown"] = self._build_overview_markdown(normalized, payload)
        normalized["findings_markdown"] = self._build_findings_markdown(normalized)
        normalized["errors_markdown"] = self._build_errors_markdown(normalized)
        normalized["quality_report_markdown"] = self._build_quality_markdown(normalized)
        normalized["review_markdown"] = self._build_combined_markdown(
            normalized["overview_markdown"],
            normalized["findings_markdown"],
            normalized["errors_markdown"],
            normalized["quality_report_markdown"],
        )
        return normalized

    def get_response(self, payload):
        user_prompt = "\n".join([
            "Review the following source file using the deterministic code review metric.",
            "",
            _source_scope_summary(payload),
            "",
            "### Source For Review",
            payload.get("source_for_model", "") or "[No source loaded]",
        ])

        try:
            parsed = call_llm_and_parse_json(self.SYSTEM_PROMPT, user_prompt, task=config.TASK_CHAT)
        except Exception as exc:
            parsed = self._fallback_review(payload, str(exc))
        return self._normalize_response(parsed, payload)


