"""Graphlink-Reasoning's agent, redesigned from a 4-raw-text-prompt, up-to-22-call
pipeline into 1 (plan) + budget x 1 (merged reason+critique) + 1 (synthesize) structured
JSON calls (see doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.10).

Follows the structured-JSON-agent pattern already proven in this codebase
(QualityGateAnalyzer, CodeReviewAnalyzer, WorkflowArchitectAgent): a system prompt asking
for an exact JSON shape, a _normalize_X method that defensively coerces every field with
typed fallbacks, and a deterministic _fallback_X heuristic used when the JSON shape is
unusable.

Deliberately DIFFERENT from those three in one respect: a genuine api_provider.chat()
failure (network down, model unavailable) is allowed to propagate and abort the whole
run, exactly like the original run_step() did - only a malformed/unparseable JSON SHAPE
falls back to a heuristic and continues. For a single-call plugin, falling back to a
heuristic on total infra failure is a fine trade (the user still gets something useful).
For this multi-call pipeline, silently degrading every one of up to 10 steps to
placeholder text - with the final synthesis built entirely from placeholders and zero
indication anything was wrong - would be a worse experience than just surfacing the real
error once, immediately.
"""

import json

import graphite_config as config
import api_provider
from graphite_plugins.common.llm_json import extract_json_object


MAX_PLAN_STEPS = 12
MAX_THOUGHT_HISTORY_ENTRIES = 8
MAX_THOUGHT_HISTORY_CHARS_PER_ENTRY = 1200


def _clean_text(value, limit=None):
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _truncate_thought_history(thought_history):
    # Bounds thought_history growth across a long run instead of letting it grow
    # unboundedly into every subsequent prompt (mirrors WorkflowArchitectAgent's
    # get_response: history[-8:] plus a per-message character cap).
    recent = list(thought_history)[-MAX_THOUGHT_HISTORY_ENTRIES:]
    cleaned = [_clean_text(entry, limit=MAX_THOUGHT_HISTORY_CHARS_PER_ENTRY) for entry in recent]
    cleaned = [entry for entry in cleaned if entry]
    return "\n\n".join(cleaned) if cleaned else "No thoughts yet."


class ReasoningAgent:
    """
    An agent that uses a multi-step "plan, reason+critique, synthesize" process to solve
    complex problems that a single LLM call might fail on.
    """

    PLAN_SYSTEM_PROMPT = """
You are a methodical planner. Your task is to break down a complex user query into a
series of simple, actionable steps that build towards a final answer.

Rules:
1. Each step should represent a single, self-contained thought process or question.
2. Do NOT attempt to answer the query yet - only plan the steps.
3. The plan should logically flow from one step to the next.
4. The final step should always be to synthesize the previous steps into a final answer.
5. Output valid JSON only. No markdown fences, no preamble.

Return exactly this shape:
{
  "steps": [
    {"title": "Short imperative step title", "goal": "What this step must accomplish"}
  ]
}
"""

    REASON_CRITIQUE_SYSTEM_PROMPT = """
You are a reasoning engine executing one step of a larger plan, and then critiquing your
own work.

You will be given the branch context, the original query, the thought history from
previous steps, and the current step to execute.

Rules:
1. Focus EXCLUSIVELY on the CURRENT step - do not solve the entire problem.
2. First produce a detailed, self-contained initial answer for the current step.
3. Then critique that initial answer: check for logical fallacies, missing information,
   incorrect assumptions, or a simpler explanation.
4. Then produce a refined thought that incorporates the critique - more robust, logical,
   and complete than the initial answer.
5. Output valid JSON only. No markdown fences, no preamble.

Return exactly this shape:
{
  "initial_thought": "The first-pass answer to this step",
  "critique": "Brief bulleted self-critique of the initial thought",
  "refined_thought": "The improved, final thought for this step"
}
"""

    SYNTHESIZE_SYSTEM_PROMPT = """
You are a synthesis expert. You have been provided with branch context, a user's
original query, and a series of vetted, refined thoughts that break down the problem.

Rules:
- Weave these thoughts together into a single, comprehensive, well-structured final answer.
- Do not just list the thoughts; synthesize them into a coherent narrative that directly
  addresses the user's original query.
- Use clear markdown formatting (headings, lists, bold text) for readability.
- The final answer should be self-contained and understandable without needing to read
  the intermediate thoughts.
"""

    def _fallback_plan(self, original_prompt):
        return {
            "steps": [
                {
                    "title": "Analyze the query",
                    "goal": original_prompt or "Answer the user's question as directly as possible.",
                }
            ]
        }

    def _normalize_plan(self, parsed, original_prompt):
        if not isinstance(parsed, dict):
            return self._fallback_plan(original_prompt)

        raw_steps = parsed.get("steps")
        if not isinstance(raw_steps, list):
            return self._fallback_plan(original_prompt)

        steps = []
        for item in raw_steps[:MAX_PLAN_STEPS]:
            if not isinstance(item, dict):
                continue
            title = _clean_text(item.get("title"), limit=120)
            goal = _clean_text(item.get("goal"), limit=500)
            if not title and not goal:
                continue
            steps.append({"title": title or "Step", "goal": goal or title})

        if not steps:
            return self._fallback_plan(original_prompt)

        return {"steps": steps}

    def plan(self, original_prompt, branch_context):
        user_prompt = f"""
Branch Context:
{branch_context or "No prior branch context."}

Original Query:
{original_prompt}

Create a step-by-step plan to answer this query.
"""
        try:
            response = api_provider.chat(
                task=config.TASK_CHAT,
                messages=[
                    {"role": "system", "content": self.PLAN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"API call failed while planning: {exc}") from exc

        try:
            parsed = json.loads(extract_json_object(response["message"]["content"]))
        except Exception:
            parsed = None

        return self._normalize_plan(parsed, original_prompt)

    def _fallback_step_result(self, step):
        placeholder = (
            "The model's response for this step could not be used, so its planned goal "
            f"is being carried forward as-is:\n\n{step.get('goal', '')}"
        )
        return {
            "initial_thought": placeholder,
            "critique": "No critique available - the model response for this step was unusable.",
            "refined_thought": placeholder,
        }

    def _normalize_step_result(self, parsed):
        if not isinstance(parsed, dict):
            return None

        initial_thought = _clean_text(parsed.get("initial_thought"))
        critique = _clean_text(parsed.get("critique"))
        # A missing/empty "refined thought" falls back to the initial thought specifically
        # - never to the raw parsed dict or a hunted-for substring of unrelated text (the
        # structural fix for the old critique-parsing pollution bug).
        refined_thought = _clean_text(parsed.get("refined_thought")) or initial_thought

        if not initial_thought and not refined_thought:
            return None

        return {
            "initial_thought": initial_thought or refined_thought,
            "critique": critique or "No specific issues identified.",
            "refined_thought": refined_thought or initial_thought,
        }

    def reason_and_critique(self, original_prompt, branch_context, step, thought_history):
        history_text = _truncate_thought_history(thought_history)
        user_prompt = f"""
Branch Context:
{branch_context or "No prior branch context."}

Original Query:
{original_prompt}

Thought History:
{history_text}

CURRENT STEP TO EXECUTE:
Title: {step.get('title', '')}
Goal: {step.get('goal', '')}
"""
        try:
            response = api_provider.chat(
                task=config.TASK_CHAT,
                messages=[
                    {"role": "system", "content": self.REASON_CRITIQUE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"API call failed during step '{step.get('title', '')}': {exc}") from exc

        try:
            parsed = json.loads(extract_json_object(response["message"]["content"]))
        except Exception:
            parsed = None

        normalized = self._normalize_step_result(parsed)
        if normalized is None:
            return self._fallback_step_result(step)
        return normalized

    def synthesize(self, original_prompt, branch_context, thought_history):
        history_text = _truncate_thought_history(thought_history)
        user_prompt = f"""
Branch Context:
{branch_context or "No prior branch context."}

Original Query:
{original_prompt}

Full History of Refined Thoughts:
{history_text}
"""
        try:
            response = api_provider.chat(
                task=config.TASK_CHAT,
                messages=[
                    {"role": "system", "content": self.SYNTHESIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"API call failed during synthesis: {exc}") from exc
