"""Provider-neutral model metadata and task-routing helpers.

The settings UI and the request runtime used to pass around model IDs as opaque
strings.  This module keeps the public model ID deliberately small while giving
the rest of the application a stable place for readiness, capability, and
selection semantics.  Provider adapters can add richer metadata without making
the settings layer aware of a provider SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, NamedTuple


AUTO_MODEL = "auto"
INHERIT_MODEL = "inherit"

CAPABILITY_TEXT = "text"
CAPABILITY_CODE = "code"
CAPABILITY_VISION = "vision"
CAPABILITY_AUDIO = "audio"
CAPABILITY_TOOLS = "tools"
CAPABILITY_REASONING = "reasoning"
CAPABILITY_IMAGE = "image"


TASK_REQUIREMENTS = {
    "task_title": frozenset({CAPABILITY_TEXT}),
    "task_chat": frozenset({CAPABILITY_TEXT}),
    "task_chart": frozenset({CAPABILITY_TEXT, CAPABILITY_CODE}),
    "task_image_gen": frozenset({CAPABILITY_IMAGE}),
    "task_web_validate": frozenset({CAPABILITY_TEXT}),
    "task_web_summarize": frozenset({CAPABILITY_TEXT}),
}


@dataclass(frozen=True)
class ModelDescriptor:
    """A display and routing description for one model ID.

    ``ready`` is intentionally separate from ``available``: cloud catalog
    entries can be selectable even when their endpoint is currently offline,
    while a local model must be installed before it can be used.
    """

    model_id: str
    provider: str = ""
    ready: bool = True
    available: bool = True
    capabilities: frozenset[str] = field(default_factory=frozenset)
    source: str = "catalog"
    size_bytes: int | None = None
    context_length: int | None = None
    quantization: str = ""
    details: Mapping[str, object] = field(default_factory=dict)
    error: str = ""
    # ADR-018 stage 18.1: USD per million tokens, from ADR-016's pricing
    # table (backend/token_counter.py) - kept as plain floats here rather
    # than importing token_counter, which this Qt-free root module must
    # not depend on (see this module's own "zero imports beyond stdlib"
    # posture, mirrored by graphlink_task_config.py's docstring on why it
    # imports FROM here and never the other direction). None means "no
    # pricing data for this model", never "free" - unified_catalog() is the
    # only place that fills these in, via a caller-supplied price_lookup.
    cost_input_per_mtok: float | None = None
    cost_output_per_mtok: float | None = None
    # "" (unknown) | "fast" | "standard" | "slow" - best-effort, unset by
    # every source today; a future discovery pass (observed time-to-first-
    # token) is the real writer. choose_auto_model_ref's "fastest" policy
    # treats "" as slowest-of-the-unknowns, never assumed fast.
    latency_class: str = ""

    def supports(self, required: Iterable[str]) -> bool:
        required = set(required or ())
        if not required:
            return True
        # Unknown capability metadata should not make a model disappear from
        # the picker.  The runtime/provider remains the final authority.
        if not self.capabilities:
            return True
        return required.issubset(self.capabilities)

    @property
    def display_name(self) -> str:
        return self.model_id


@dataclass(frozen=True)
class ModelAssignment:
    """Persistable task assignment with explicit inheritance semantics."""

    mode: str = AUTO_MODEL
    model_id: str = ""

    @classmethod
    def from_value(cls, value) -> "ModelAssignment":
        if isinstance(value, Mapping):
            mode = str(value.get("mode", AUTO_MODEL) or AUTO_MODEL).strip().lower()
            model_id = normalize_model_id(value.get("model_id", value.get("model", "")))
            if mode == "explicit" and not model_id:
                mode = AUTO_MODEL
            if mode not in {AUTO_MODEL, INHERIT_MODEL, "explicit"}:
                mode = AUTO_MODEL
            return cls(mode, model_id)

        model_id = normalize_model_id(value)
        if not model_id or model_id.lower() in {AUTO_MODEL, INHERIT_MODEL}:
            return cls(AUTO_MODEL if model_id != INHERIT_MODEL else INHERIT_MODEL)
        return cls("explicit", model_id)

    def to_dict(self) -> dict[str, str]:
        return {"mode": self.mode, "model_id": self.model_id}


def normalize_model_id(value) -> str:
    return str(value or "").strip()


def normalize_assignments(values: Mapping | None) -> dict[str, ModelAssignment]:
    values = values if isinstance(values, Mapping) else {}
    return {str(task): ModelAssignment.from_value(value) for task, value in values.items()}


def assignment_values(values: Mapping | None) -> dict[str, dict[str, str]]:
    return {
        task: assignment.to_dict()
        for task, assignment in normalize_assignments(values).items()
    }


def _field(value, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _context_length_from_ollama(model, details) -> int | None:
    """ADR-006 stage 6.6: best-effort context length for an Ollama model.

    ``list()`` entries carry it (rarely) in details; ``show()`` responses
    carry it in model_info as "<arch>.context_length". Best-effort only -
    the request path budgets via ProviderRuntime.context_window(), not the
    catalog; this just stops the descriptor field being permanently dead."""
    direct = _as_int(_field(details, "context_length"))
    if direct:
        return direct
    model_info = _field(model, "model_info") or _field(model, "modelinfo")
    if isinstance(model_info, Mapping):
        for key, value in model_info.items():
            if str(key).endswith(".context_length"):
                parsed = _as_int(value)
                if parsed:
                    return parsed
    return None


def ollama_descriptor(model, *, provider: str = "Ollama") -> ModelDescriptor:
    """Normalize an Ollama ``list()``/``show()`` result into a descriptor."""

    model_id = normalize_model_id(_field(model, "model") or _field(model, "name"))
    details = _field(model, "details", {}) or {}
    capabilities = set()
    raw_capabilities = _field(model, "capabilities") or _field(details, "capabilities", [])
    if isinstance(raw_capabilities, str):
        raw_capabilities = [raw_capabilities]
    for capability in raw_capabilities or ():
        normalized = str(capability).strip().lower().replace("-", "_")
        aliases = {
            "embedding": CAPABILITY_TEXT,
            "completion": CAPABILITY_TEXT,
            "image_generation": CAPABILITY_IMAGE,
            "image": CAPABILITY_VISION,
            "vision": CAPABILITY_VISION,
            "tool": CAPABILITY_TOOLS,
            "function_calling": CAPABILITY_TOOLS,
        }
        capabilities.add(aliases.get(normalized, normalized))

    family = str(_field(details, "family", "") or "").lower()
    if family:
        capabilities.add(CAPABILITY_TEXT)
        if "code" in family or "coder" in family:
            capabilities.add(CAPABILITY_CODE)
    if _field(details, "parameter_size"):
        # A model with Ollama details is at least a usable text model unless
        # the provider explicitly reports another modality.
        capabilities.add(CAPABILITY_TEXT)

    return ModelDescriptor(
        model_id=model_id,
        provider=provider,
        ready=True,
        available=True,
        capabilities=frozenset(capabilities),
        source="installed",
        size_bytes=_as_int(_field(model, "size")),
        context_length=_context_length_from_ollama(model, details),
        quantization=str(_field(details, "quantization_level", "") or ""),
        details=dict(details) if isinstance(details, Mapping) else {},
    )


def sort_descriptors(descriptors: Iterable[ModelDescriptor]) -> list[ModelDescriptor]:
    unique: dict[tuple[str, str], ModelDescriptor] = {}
    for descriptor in descriptors or ():
        if not isinstance(descriptor, ModelDescriptor):
            continue
        key = (descriptor.provider.lower(), descriptor.model_id.lower())
        if key[1] and (key not in unique or descriptor.ready):
            unique[key] = descriptor
    return sorted(
        unique.values(),
        key=lambda item: (not item.ready, not item.available, item.model_id.lower()),
    )


def choose_auto_model(
    task: str,
    catalog: Iterable[ModelDescriptor],
    *,
    preferred_model: str = "",
) -> str:
    """Choose a deterministic ready model without provider-specific defaults."""

    candidates = [
        item
        for item in sort_descriptors(catalog)
        if item.ready and item.available and item.supports(TASK_REQUIREMENTS.get(task, ()))
    ]
    if not candidates:
        return ""
    preferred_model = normalize_model_id(preferred_model).lower()
    if preferred_model:
        for item in candidates:
            if item.model_id.lower() == preferred_model:
                return item.model_id
    # Prefer a known text/capability match, then stable alphabetical order.
    candidates.sort(key=lambda item: (not bool(item.capabilities), item.model_id.lower()))
    return candidates[0].model_id


def resolve_task_model(
    task: str,
    assignments: Mapping | None,
    catalog: Iterable[ModelDescriptor] = (),
    *,
    chat_model: str = "",
) -> str:
    """Resolve explicit, inherited, or automatic task routing."""

    normalized = normalize_assignments(assignments)
    assignment = normalized.get(task, ModelAssignment())
    if assignment.mode == "explicit" and assignment.model_id:
        return assignment.model_id
    if assignment.mode == INHERIT_MODEL:
        chat_assignment = normalized.get("task_chat", ModelAssignment())
        if chat_assignment.mode == "explicit" and chat_assignment.model_id:
            return chat_assignment.model_id
        if chat_model:
            return normalize_model_id(chat_model)
    return choose_auto_model(task, catalog, preferred_model=chat_model if task != "task_chat" else "")


# -- ADR-018: ModelRef dispatch, the resolution chain, auto policies --------


@dataclass(frozen=True)
class ModelRef:
    """A resolved (provider, model) pair - the unit of dispatch ADR-018
    replaces task-keyed globals with. `provider` uses this codebase's own
    provider constant VALUES (graphlink_task_config.LOCAL_PROVIDER_OLLAMA
    == "Ollama", API_PROVIDER_ANTHROPIC == "Anthropic Claude", etc.) as
    plain strings rather than importing that module's names - this module
    stays dependency-free (task_config imports FROM here, never the
    reverse; see that module's own docstring), and every provider-select
    branch this ADR touches (api_provider.py) already switches on those
    exact string values, so no translation layer is needed anywhere."""

    provider: str
    model_id: str


class ResolvedModel(NamedTuple):
    """A ModelRef plus WHICH rung of the resolution chain produced it -
    ADR-018 decision #3's inspectability requirement ("the UI can always
    answer 'why this model?'"). `rung` is one of "node override", "branch
    override", "workspace default", or "auto: <policy>"."""

    ref: ModelRef
    rung: str


AUTO_POLICY_CHEAPEST_CAPABLE = "cheapest-capable"
AUTO_POLICY_FASTEST = "fastest"
AUTO_POLICY_BEST_QUALITY = "best-quality"
AUTO_POLICIES = (AUTO_POLICY_CHEAPEST_CAPABLE, AUTO_POLICY_FASTEST, AUTO_POLICY_BEST_QUALITY)

# ADR-018 stage 18.5: tasks where a retryable/unavailable request failure
# (ADR-006 section 6's transient-transport classification, exhausted) falls
# back to a DIFFERENT provider instead of surfacing the error - "naming/
# triage" per the ADR's own "off by default for correctness-sensitive
# tasks, on by default for naming/triage" framing. task_title is literally
# naming; task_web_validate is the web-research pipeline's fast per-
# document relevance triage. Every other task (task_chat's own visible
# reply, task_chart's generated code, task_image_gen's specific request,
# task_web_summarize's fidelity to source text) is correctness-sensitive -
# a silent model swap there would corrupt exactly the model-comparison
# workflow this ADR's own "Alternatives considered" section rejects
# enabling by default for.
FALLBACK_ENABLED_TASKS = frozenset({"task_title", "task_web_validate"})

_LATENCY_RANK = {"fast": 0, "standard": 1, "slow": 2, "": 3}


def _known_cost(descriptor: ModelDescriptor) -> float | None:
    if descriptor.cost_input_per_mtok is None or descriptor.cost_output_per_mtok is None:
        return None
    return descriptor.cost_input_per_mtok + descriptor.cost_output_per_mtok


def choose_auto_model_ref(
    catalog: Iterable[ModelDescriptor],
    required_capabilities: Iterable[str] = (),
    *,
    policy: str = AUTO_POLICY_CHEAPEST_CAPABLE,
) -> ModelRef | None:
    """The auto rung: a policy over the catalog, never an LLM deciding
    (ADR-018 decision #3). Capability-filtered FIRST, always - the one
    invariant every policy shares, so a vision request can never resolve
    to a text-only model regardless of which policy is active. Returns
    None when nothing in the catalog is ready/available/capable; the
    caller turns that into an actionable error, never a silent guess."""

    required = frozenset(required_capabilities or ())
    candidates = [
        d for d in sort_descriptors(catalog)
        if d.ready and d.available and d.supports(required)
    ]
    if not candidates:
        return None

    if policy == AUTO_POLICY_FASTEST:
        candidates.sort(key=lambda d: (_LATENCY_RANK.get(d.latency_class, 3), d.model_id.lower()))
    elif policy == AUTO_POLICY_BEST_QUALITY:
        # No independent "quality" signal exists in the catalog - cost is
        # used as the proxy the ADR's own context section frames ("a cheap
        # fast model for naming... a strong model for reasoning"), so the
        # priciest KNOWN-cost model wins. Unknown cost sorts last, never
        # assumed to be the best.
        def _quality_key(d: ModelDescriptor):
            cost = _known_cost(d)
            return (0, -cost) if cost is not None else (1, 0.0)
        candidates.sort(key=lambda d: (_quality_key(d), d.model_id.lower()))
    else:
        # AUTO_POLICY_CHEAPEST_CAPABLE (also the fallback for an unknown
        # policy string - cost-based is the safer default to fail toward).
        # A genuinely free local model (cost 0.0+0.0) always wins; among
        # KNOWN nonzero costs the cheapest wins; unknown cost sorts last -
        # never assumed free, which would make an unpriced cloud model look
        # artificially cheaper than a priced one.
        def _cost_key(d: ModelDescriptor):
            cost = _known_cost(d)
            if cost is None:
                return (2, 0.0)
            return (0, 0.0) if cost == 0.0 else (1, cost)
        candidates.sort(key=lambda d: (_cost_key(d), d.model_id.lower()))

    chosen = candidates[0]
    return ModelRef(provider=chosen.provider, model_id=chosen.model_id)


def resolve_model_ref(
    task: str,
    *,
    node_ref: ModelRef | None = None,
    branch_ref: ModelRef | None = None,
    workspace_ref: ModelRef | None = None,
    catalog: Iterable[ModelDescriptor] = (),
    auto_policy: str = AUTO_POLICY_CHEAPEST_CAPABLE,
    required_capabilities: Iterable[str] | None = None,
) -> ResolvedModel | None:
    """The chain ADR-018 decision #3 specifies: node override -> branch
    override -> workspace default for task -> auto(policy). Returns None
    when every rung is empty AND auto can't resolve either - the caller's
    signal to raise an actionable error rather than dispatch with nothing.

    Deliberately NOT capability-filtering the first three rungs: those are
    a human's (or an inherited human's) explicit pin, and this module's own
    ModelDescriptor.supports() docstring already establishes the posture
    that missing/mismatched capability metadata must never make an
    explicit choice disappear - "the runtime/provider remains the final
    authority". Only the auto rung, which picks on the user's behalf,
    enforces the filter (see choose_auto_model_ref)."""

    for ref, rung in (
        (node_ref, "node override"),
        (branch_ref, "branch override"),
        (workspace_ref, "workspace default"),
    ):
        if ref is not None and ref.provider and ref.model_id:
            return ResolvedModel(ref, rung)

    required = (
        required_capabilities if required_capabilities is not None
        else TASK_REQUIREMENTS.get(task, frozenset())
    )
    auto_ref = choose_auto_model_ref(catalog, required, policy=auto_policy)
    if auto_ref is None:
        return None
    return ResolvedModel(auto_ref, f"auto: {auto_policy}")


# Literal provider-constant values duplicated from graphlink_task_config.py
# (see ModelRef's own docstring for why this module cannot import that one).
# Kept as a tuple, not individual names, so unified_catalog stays a single
# short loop rather than five near-identical blocks.
_OLLAMA = "Ollama"
_LLAMACPP = "Llama.cpp"
_API_PROVIDERS = ("OpenAI-Compatible", "Anthropic Claude", "Google Gemini")


def unified_catalog(
    settings_manager,
    *,
    price_lookup: Callable[[str, str], tuple[float, float] | None] | None = None,
) -> list[ModelDescriptor]:
    """ADR-018 stage 18.1: one list spanning every configured provider -
    "graphlink_model_catalog.py becomes the single catalog for every
    provider" (the ADR's own decision #2). A pure aggregation over already
    -cached discovery data (Ollama/llama.cpp scan results, each API
    provider's last catalog refresh) - it makes no network calls itself,
    so it is cheap enough to call on the resolution hot path. `price_lookup
    (provider, model_id) -> (input_usd_per_mtok, output_usd_per_mtok) | None`
    is caller-supplied (backend/token_counter.py's pricing table) rather
    than imported, for the same dependency-direction reason ModelRef's
    docstring gives."""

    if settings_manager is None:
        return []

    descriptors: list[ModelDescriptor] = []

    ollama_models = settings_manager.get_ollama_scanned_models() or ()
    for model_id in ollama_models:
        model_id = normalize_model_id(model_id)
        if model_id:
            descriptors.append(ModelDescriptor(model_id=model_id, provider=_OLLAMA, source="installed"))

    llama_cpp_models = settings_manager.get_llama_cpp_scanned_models() or ()
    for model_path in llama_cpp_models:
        # Scanned entries are full filesystem paths (SettingsManager's own
        # scan-results shape) - reduced to a basename here to match the
        # ONLY identity _provider_for_model_ref's llama.cpp branch (and
        # describe_active_model's own display convention) actually accepts:
        # llama.cpp has no "load any installed model by id" catalog the way
        # Ollama does, so the two currently-configured paths' own basenames
        # are the entire addressable set. A full-path model_id here would
        # never match and every auto/fallback pick would be rejected.
        model_id = normalize_model_id(Path(model_path).name if model_path else "")
        if model_id:
            descriptors.append(ModelDescriptor(model_id=model_id, provider=_LLAMACPP, source="installed"))

    for provider in _API_PROVIDERS:
        catalog_entries = settings_manager.get_api_model_catalog(provider) or ()
        for entry in catalog_entries:
            model_id = normalize_model_id(entry.get("model_id") if isinstance(entry, Mapping) else "")
            if not model_id:
                continue
            capabilities = entry.get("capabilities") if isinstance(entry, Mapping) else None
            descriptors.append(ModelDescriptor(
                model_id=model_id,
                provider=provider,
                ready=bool(entry.get("ready", True)) if isinstance(entry, Mapping) else True,
                available=bool(entry.get("available", True)) if isinstance(entry, Mapping) else True,
                capabilities=frozenset(capabilities or ()),
                source="catalog",
            ))

    if price_lookup is not None:
        priced: list[ModelDescriptor] = []
        for descriptor in descriptors:
            prices = price_lookup(descriptor.provider, descriptor.model_id)
            if prices is None:
                priced.append(descriptor)
            else:
                cost_in, cost_out = prices
                priced.append(replace(descriptor, cost_input_per_mtok=cost_in, cost_output_per_mtok=cost_out))
        descriptors = priced

    return sort_descriptors(descriptors)
