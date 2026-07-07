"""Quality Gate scoring/rubric engine, extracted out of graphite_plugin_quality_gate.py
(see doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.7) so it can be unit tested without
constructing any Qt widget or GUI application object.

Deliberately NOT merged with graphite_plugins.code_review.scoring.CodeReviewAnalyzer -
the two rubrics score genuinely different things (branch release-readiness vs. code
quality) and only superficially resemble each other (both call an LLM and fall back to
a heuristic when it's unavailable).
"""

import re

import graphite_config as config
from graphite_plugins.common.llm_json import call_llm_and_parse_json, extract_json_object


QUALITY_GATE_PLUGIN_ICONS = {
    "Py-Coder": "fa5s.code",
    "Gitlink": "fa5s.link",
    "Execution Sandbox": "fa5s.shield-alt",
    "Artifact / Drafter": "fa5s.file-alt",
    "Graphlink-Web": "fa5s.globe-americas",
    "Conversation Node": "fa5s.comments",
    "Graphlink-Reasoning": "fa5s.brain",
    "HTML Renderer": "fa5s.window-maximize",
    "Workflow Architect": "fa5s.project-diagram",
}

QUALITY_GATE_ALLOWED_PLUGINS = list(QUALITY_GATE_PLUGIN_ICONS.keys())


def _flatten_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(part for part in parts if part)
    return str(content)


def _clean_text(text, limit=2500):
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _read_widget_text(widget):
    if not widget:
        return ""

    for method_name in ("toPlainText", "text", "toHtml"):
        method = getattr(widget, method_name, None)
        if callable(method):
            try:
                value = method()
                if isinstance(value, str):
                    return value
            except Exception:
                pass
    return ""


_NON_PLUGIN_NODE_LABELS = {
    # Core node types that aren't registered plugins (see PLUGIN_REGISTRY in
    # graphite_plugin_portal.py) but can still appear in a branch transcript.
    "ChatNode": "Chat Node",
}


def _node_label(node):
    class_name = node.__class__.__name__
    if class_name in _NON_PLUGIN_NODE_LABELS:
        return _NON_PLUGIN_NODE_LABELS[class_name]

    # Deferred import: graphite_plugin_portal imports graphite_plugin_quality_gate (to
    # register the Quality Gate plugin), so importing it back at module load time would
    # be circular. By the time _node_label() actually runs, both modules are loaded.
    from graphite_plugins.graphite_plugin_portal import get_display_name_for_node

    return get_display_name_for_node(node)


def _extract_node_text(node):
    parts = []

    if hasattr(node, "text") and isinstance(getattr(node, "text"), str):
        parts.append(node.text)

    if hasattr(node, "conversation_history"):
        for message in getattr(node, "conversation_history", [])[-6:]:
            role = message.get("role", "unknown").title()
            content = _flatten_content(message.get("content", ""))
            if content.strip():
                parts.append(f"{role}: {content.strip()}")

    for attr in ("prompt", "thinking_text", "thought_process", "blueprint_markdown", "review_markdown", "html_content"):
        value = getattr(node, attr, "")
        if isinstance(value, str) and value.strip():
            parts.append(value)

    for getter_name, prefix in (
        ("get_goal", "Goal"),
        ("get_constraints", "Constraints"),
        ("get_criteria", "Acceptance Criteria"),
        ("get_artifact_content", "Artifact"),
        ("get_requirements", "Requirements"),
        ("get_code", "Code"),
    ):
        getter = getattr(node, getter_name, None)
        if callable(getter):
            try:
                value = getter().strip()
                if value:
                    parts.append(f"{prefix}:\n{value}")
            except Exception:
                pass

    for widget_name in (
        "query_input",
        "prompt_input",
        "instruction_input",
        "message_input",
        "output_display",
        "ai_analysis_display",
    ):
        widget = getattr(node, widget_name, None)
        value = _read_widget_text(widget).strip()
        if value:
            parts.append(value)

    if hasattr(node, "summary_text") and isinstance(getattr(node, "summary_text"), str):
        if node.summary_text.strip():
            parts.append(node.summary_text)

    unique_parts = []
    seen = set()
    for part in parts:
        cleaned = _clean_text(part, limit=1200)
        if cleaned and cleaned not in seen:
            unique_parts.append(cleaned)
            seen.add(cleaned)
    return "\n\n".join(unique_parts)


def _collect_branch_nodes(node):
    lineage = []
    seen = set()
    cursor = node
    while cursor and id(cursor) not in seen:
        lineage.append(cursor)
        seen.add(id(cursor))
        cursor = getattr(cursor, "parent_node", None)
    lineage.reverse()
    return lineage


def build_quality_gate_payload(node, include_branch_context=True):
    lineage = _collect_branch_nodes(node) if include_branch_context else ([node] if node else [])
    sections = []
    labels = []

    for index, branch_node in enumerate(lineage, start=1):
        label = _node_label(branch_node)
        labels.append(label)
        content = _extract_node_text(branch_node)
        if not content:
            continue
        sections.append(f"Step {index}: {label}\n{content}")

    transcript = "\n\n---\n\n".join(sections) if sections else "No branch transcript available."
    preview = "\n\n".join(sections[-3:]) if sections else transcript
    branch_label = _node_label(node) if node else "Current Branch"

    return {
        "label": branch_label,
        "depth": len(lineage),
        "node_labels": labels,
        "transcript": _clean_text(transcript, limit=14000),
        "preview": _clean_text(preview, limit=3200),
    }


class QualityGateAnalyzer:
    SYSTEM_PROMPT = """
You are Graphlink's Quality Gate.
Your job is to evaluate whether the current branch is actually ready for production, release, or stakeholder handoff.

Allowed follow-up plugins:
- Py-Coder
- Gitlink
- Execution Sandbox
- Artifact / Drafter
- Graphlink-Web
- Conversation Node
- Graphlink-Reasoning
- HTML Renderer
- Workflow Architect

Rules:
1. Be tough but fair. Do not confuse momentum with readiness.
2. Judge against explicit acceptance criteria first, then against missing evidence and branch quality.
3. If evidence is missing, say so directly.
4. Recommend at most 4 follow-up plugins.
5. Only recommend HTML Renderer when UI or rendered output is directly relevant.
6. Use verdict values only from: ready, needs_work, blocked.
7. Output valid JSON only. No markdown fences, no preamble.

Return exactly this shape:
{
  "title": "Short review title",
  "verdict": "ready",
  "readiness_score": 88,
  "overview": "2-4 sentence review summary",
  "strengths": ["..."],
  "blockers": ["..."],
  "risks": ["..."],
  "missing_evidence": ["..."],
  "recommended_plugins": [
    {
      "plugin": "Py-Coder",
      "priority": "high",
      "why": "Why this plugin closes the highest-value gap",
      "starter_prompt": "The exact seeded instruction for that plugin"
    }
  ],
  "next_actions": ["..."],
  "release_decision": "One concise release recommendation",
  "note_summary": "Concise summary suitable for a note"
}
"""

    def _clean_json_response(self, raw_text):
        # Delegates to the shared regex (doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section
        # 1.6/3.5) - kept as a wrapper so this method's existing call site is unchanged.
        return extract_json_object(raw_text)

    def _fallback_review(self, goal, criteria, payload):
        transcript = payload.get("transcript", "")
        lowered = f"{goal}\n{criteria}\n{transcript}".lower()

        has_code = bool(re.search(r"\b(def |class |import |function|python|script|html|css|javascript|tsx|sql|```)\b", transcript, re.IGNORECASE))
        has_tests = bool(re.search(r"\b(test|tests|pytest|unit test|integration test|assert|verification|validated)\b", transcript, re.IGNORECASE))
        has_errors = bool(re.search(r"\b(error|failed|failure|traceback|exception|bug|broken)\b", transcript, re.IGNORECASE))
        has_sources = bool(re.search(r"\b(source|sources|http|www\\.|citation|citations|reference|references|research)\b", transcript, re.IGNORECASE))
        has_plan = bool(re.search(r"\b(plan|workflow|step|deliverable|mission|roadmap|sequence)\b", transcript, re.IGNORECASE))
        has_artifact = bool(re.search(r"\b(spec|brief|proposal|report|document|markdown|artifact)\b", transcript, re.IGNORECASE))
        repo_goal = any(term in lowered for term in ("repo", "repository", "github", "git", "codebase", "checkout", "monorepo", "branch"))
        build_goal = any(term in lowered for term in ("build", "implement", "ship", "production", "release", "feature", "fix", "app", "ui"))
        research_goal = any(term in lowered for term in ("research", "latest", "market", "current", "trend", "competitor", "news"))
        doc_goal = any(term in lowered for term in ("spec", "proposal", "brief", "report", "doc", "documentation"))

        score = 46
        if len(transcript) > 450:
            score += 8
        if has_plan:
            score += 8
        if has_code:
            score += 12
        if has_tests:
            score += 14
        if has_sources:
            score += 8
        if has_artifact:
            score += 6
        if criteria.strip():
            score += 8
        else:
            score -= 10
        if has_errors:
            score -= 18
        if len(transcript) < 240:
            score -= 12
        score = max(8, min(96, score))

        strengths = []
        blockers = []
        risks = []
        missing_evidence = []
        recommendations = []

        def add(plugin, why, starter_prompt, priority="medium"):
            if plugin in QUALITY_GATE_ALLOWED_PLUGINS and not any(item["plugin"] == plugin for item in recommendations):
                recommendations.append({
                    "plugin": plugin,
                    "priority": priority,
                    "why": why,
                    "starter_prompt": starter_prompt,
                })

        if has_plan:
            strengths.append("The branch already shows structured intent instead of staying purely exploratory.")
        if has_code:
            strengths.append("There is concrete implementation or executable detail to review.")
        if has_tests:
            strengths.append("Verification language is present, which raises confidence in branch quality.")
        if has_sources:
            strengths.append("The branch includes external grounding or evidence rather than relying only on intuition.")
        if has_artifact:
            strengths.append("Important decisions are captured in a more durable artifact, not just transient chat output.")
        if not strengths:
            strengths.append("The branch has a visible direction and can be hardened from here.")

        if not criteria.strip():
            blockers.append("Acceptance criteria are not explicit yet, so readiness cannot be judged against a stable shipping bar.")
            missing_evidence.append("A concrete release checklist or acceptance bar is missing.")

        if build_goal and not has_code:
            blockers.append("The goal sounds implementation-oriented, but the branch does not yet show enough concrete build evidence.")
            missing_evidence.append("Implementation evidence is still too thin for a production verdict.")

        if has_code and not has_tests:
            blockers.append("Implementation exists, but there is no clear verification or test evidence yet.")
            missing_evidence.append("A reproducible validation pass or test result is missing.")

        if has_errors:
            blockers.append("The branch still contains failure/error signals that suggest unresolved issues remain.")

        if research_goal and not has_sources:
            risks.append("Research assumptions may be stale or ungrounded because no clear source evidence is attached.")
            missing_evidence.append("Source-backed evidence for the key factual claims is missing.")

        if doc_goal and not has_artifact:
            risks.append("The deliverable appears document-heavy, but the polished artifact has not been consolidated yet.")

        if not has_plan:
            risks.append("Without a tighter execution sequence, the next branch steps may drift or duplicate effort.")

        if len(transcript) < 240:
            risks.append("The branch is still sparse enough that a confident production-style verdict would be premature.")

        if not blockers and score >= 84:
            verdict = "ready"
        elif has_errors or score < 52:
            verdict = "blocked"
        else:
            verdict = "needs_work"

        if not criteria.strip():
            add(
                "Artifact / Drafter",
                "A release review needs a visible acceptance checklist before the branch can be judged fairly.",
                f"Turn this goal into a crisp acceptance checklist and shipping bar:\n\n{goal}",
                "high",
            )

        if research_goal and not has_sources:
            add(
                "Graphlink-Web",
                "Current or external claims need evidence before the branch can be trusted.",
                goal,
                "high",
            )

        if repo_goal and (not has_code or len(transcript) < 500):
            add(
                "Gitlink",
                "This branch would benefit from explicit repository grounding before more implementation or review work happens.",
                f"Connect this branch to the relevant GitHub repository, load the most useful files, and package the repo context for the next implementation pass.\n\nGoal:\n{goal}",
                "high",
            )

        if build_goal and (not has_code or has_errors):
            plugin = "Execution Sandbox" if any(term in lowered for term in ("dependency", "dependencies", "requirements", "venv", "virtualenv", "install", "package")) else "Py-Coder"
            add(
                plugin,
                "The highest-leverage gap is still in implementation quality or execution evidence.",
                f"Close the highest-risk delivery gaps for this goal and show concrete evidence:\n\nGoal:\n{goal}\n\nAcceptance criteria:\n{criteria or 'Define the acceptance criteria first.'}",
                "high",
            )

        if has_code and not has_tests:
            add(
                "Execution Sandbox",
                "A reproducible validation pass will convert this from plausible to defensible.",
                f"Run the strongest verification pass you can for this branch and capture the results.\n\nGoal:\n{goal}\n\nAcceptance criteria:\n{criteria or 'Define the acceptance criteria first.'}",
                "high",
            )

        if not has_plan or any(term in lowered for term in ("architecture", "tradeoff", "ambiguous", "unclear", "complex")):
            add(
                "Graphlink-Reasoning",
                "The remaining risk should be decomposed and prioritized before more work is added.",
                f"Break the remaining delivery gaps into a tight remediation plan for this goal:\n\n{goal}",
                "medium",
            )

        if doc_goal and not has_artifact:
            add(
                "Artifact / Drafter",
                "A polished deliverable should be consolidated into a durable artifact before release.",
                f"Draft or refine the final artifact for this goal:\n\n{goal}",
                "medium",
            )

        if not recommendations:
            add(
                "Graphlink-Reasoning",
                "A final critique pass is the safest next move when the branch is close but not yet proven.",
                f"Review the remaining gaps and prioritize the smallest set of changes needed to finish this goal:\n\n{goal}",
                "medium",
            )

        next_actions = []
        if blockers:
            next_actions.extend(blockers[:2])
        else:
            next_actions.append("Preserve the current branch, then close only the highest-risk remaining gap.")

        for item in recommendations[:3]:
            next_actions.append(f"Use {item['plugin']} next: {item['why']}")

        next_actions = next_actions[:4]

        if verdict == "ready":
            release_decision = "Ready for production-style use, but keep one final pass for regression awareness."
        elif verdict == "blocked":
            release_decision = "Do not ship yet. The branch still lacks essential proof or contains unresolved failure signals."
        else:
            release_decision = "Promising, but not ready to ship yet. Close the highlighted gaps first."

        note_summary_lines = [
            "# Quality Gate Summary",
            "",
            f"- Verdict: {verdict.replace('_', ' ').title()}",
            f"- Readiness Score: {score} / 100",
            f"- Release Decision: {release_decision}",
        ]
        if blockers:
            note_summary_lines.append(f"- Top blocker: {blockers[0]}")
        if missing_evidence:
            note_summary_lines.append(f"- Missing evidence: {missing_evidence[0]}")

        return {
            "title": "Quality Gate Review",
            "verdict": verdict,
            "readiness_score": score,
            "overview": "This branch has momentum, but production-level confidence depends on evidence, verification, and an explicit acceptance bar rather than good intentions alone.",
            "strengths": strengths[:5],
            "blockers": blockers[:5],
            "risks": risks[:5] or ["The branch should stay focused on the highest-risk gap instead of widening scope."],
            "missing_evidence": missing_evidence[:5] or ["A final high-confidence review pass after the next material change would strengthen confidence."],
            "recommended_plugins": recommendations[:4],
            "next_actions": next_actions,
            "release_decision": release_decision,
            "note_summary": "\n".join(note_summary_lines),
        }

    def _normalize_review(self, review, goal, criteria, payload):
        if not isinstance(review, dict):
            review = {}

        normalized_recommendations = []
        for item in review.get("recommended_plugins", [])[:4]:
            if not isinstance(item, dict):
                continue
            plugin = str(item.get("plugin", "")).strip()
            if plugin not in QUALITY_GATE_ALLOWED_PLUGINS:
                continue
            why = str(item.get("why", "")).strip() or "This plugin closes an important remaining readiness gap."
            starter_prompt = str(item.get("starter_prompt", "")).strip() or goal
            priority = str(item.get("priority", "medium")).strip().lower()
            if priority not in {"high", "medium", "low"}:
                priority = "medium"
            normalized_recommendations.append({
                "plugin": plugin,
                "priority": priority,
                "why": why,
                "starter_prompt": starter_prompt,
            })

        def normalize_list(key, fallback_items):
            items = []
            for value in review.get(key, []):
                text = str(value).strip()
                if text:
                    items.append(text)
            return items or fallback_items

        verdict = str(review.get("verdict", "needs_work")).strip().lower()
        if verdict not in {"ready", "needs_work", "blocked"}:
            verdict = "needs_work"

        try:
            readiness_score = int(review.get("readiness_score", 60))
        except (TypeError, ValueError):
            readiness_score = 60
        readiness_score = max(0, min(100, readiness_score))

        normalized = {
            "title": str(review.get("title", "")).strip() or "Quality Gate Review",
            "verdict": verdict,
            "readiness_score": readiness_score,
            "overview": str(review.get("overview", "")).strip() or "This branch needs a sharper acceptance review before it can be treated as release-ready.",
            "strengths": normalize_list("strengths", ["The branch contains enough structure to support a serious review pass."]),
            "blockers": normalize_list("blockers", ["No critical blockers were identified."]) if verdict == "ready" else normalize_list("blockers", ["The branch still has unresolved gaps before release."]),
            "risks": normalize_list("risks", ["Remaining risk is manageable if the next changes stay tightly scoped."]),
            "missing_evidence": normalize_list("missing_evidence", ["No major evidence gaps were identified."]) if verdict == "ready" else normalize_list("missing_evidence", ["A stronger evidence trail is still needed before release."]),
            "recommended_plugins": normalized_recommendations,
            "next_actions": normalize_list("next_actions", ["Take the next highest-leverage step and rerun Quality Gate after a material change."]),
            "release_decision": str(review.get("release_decision", "")).strip() or "Not ready to ship yet.",
            "note_summary": str(review.get("note_summary", "")).strip(),
        }

        if not normalized["note_summary"]:
            normalized["note_summary"] = "\n".join([
                "# Quality Gate Summary",
                "",
                f"- Branch: {payload.get('label', 'Current Branch')}",
                f"- Verdict: {normalized['verdict'].replace('_', ' ').title()}",
                f"- Readiness Score: {normalized['readiness_score']} / 100",
                f"- Release Decision: {normalized['release_decision']}",
            ])

        return normalized

    def _build_markdown(self, review, payload):
        lines = [
            f"# {review['title']}",
            "",
            "## Reviewed Branch",
            f"- **Source:** {payload.get('label', 'Current Branch')}",
            f"- **Branch Depth:** {payload.get('depth', 0)} step(s)",
            "",
            "## Verdict",
            f"- **Verdict:** {review['verdict'].replace('_', ' ').title()}",
            f"- **Readiness Score:** {review['readiness_score']} / 100",
            f"- **Release Decision:** {review['release_decision']}",
            "",
            "## Overview",
            review["overview"],
            "",
            "## Strengths",
        ]

        for item in review["strengths"]:
            lines.append(f"- {item}")

        lines.extend(["", "## Blockers"])
        for item in review["blockers"]:
            lines.append(f"- {item}")

        lines.extend(["", "## Risks"])
        for item in review["risks"]:
            lines.append(f"- {item}")

        lines.extend(["", "## Missing Evidence"])
        for item in review["missing_evidence"]:
            lines.append(f"- {item}")

        lines.extend(["", "## Recommended Fix Paths"])
        if review["recommended_plugins"]:
            for item in review["recommended_plugins"]:
                lines.append(f"- **{item['plugin']}** ({item['priority'].title()}): {item['why']}")
                lines.append(f"  Starter prompt: `{item['starter_prompt']}`")
        else:
            lines.append("- No additional plugin path was required.")

        lines.extend(["", "## Next Actions"])
        for index, step in enumerate(review["next_actions"], start=1):
            lines.append(f"{index}. {step}")

        return "\n".join(lines)

    def get_response(self, goal, criteria, payload):
        user_prompt = f"""
Goal:
{goal}

Acceptance Criteria:
{criteria if criteria.strip() else "None provided."}

Reviewed Branch:
{payload.get('label', 'Current Branch')}

Branch Steps:
{', '.join(payload.get('node_labels', [])) or 'No branch steps captured.'}

Branch Transcript:
{payload.get('transcript', 'No branch transcript available.')}
"""

        try:
            parsed = call_llm_and_parse_json(self.SYSTEM_PROMPT, user_prompt, task=config.TASK_CHAT)
            normalized = self._normalize_review(parsed, goal, criteria, payload)
        except Exception:
            normalized = self._fallback_review(goal, criteria, payload)

        normalized["review_markdown"] = self._build_markdown(normalized, payload)
        return normalized
