"""Scientific packs: stable built-ins plus new file-only pack registrations."""

import re
import tomllib
from importlib import import_module
from pathlib import Path

from backend.packaging_resources import resolve_plugins_dir

from .core import Operation

PACKS = {
    "core": ("Scientific Core", "Import numeric CSV, convert measurement units and plot paired observations."),
    "mathematics": ("Mathematics", "Evaluate polynomials, integrate samples and solve linear systems."),
    "statistics": ("Statistics", "Describe samples, fit a regression line and summarize weighted observations."),
    "physics": ("Physics", "Model ideal oscillation, steady heat flow and calorimetry."),
    "astronomy": ("Astronomy", "Estimate stellar distances and blackbody peak wavelengths."),
    "engineering": ("Engineering", "Evaluate resistor networks, RC step response and thermal expansion."),
    "chemistry": ("Chemistry", "Analyze absorption measurements, transmission spectra and isotope abundances."),
    "biology": ("Biology and Ecology", "Analyze Hill numbers, beta diversity, species accumulation and quadrat surveys; fit growth, enzyme and dose-response curves; model predator–prey ecology, genetics and survival."),
    "earth": ("Earth Science", "Analyze spherical distances, geothermal gradients and seismic arrival times."),
    "environment": ("Environmental Science", "Calculate flow-weighted concentrations, runoff and growing degree days."),
    "materials": ("Materials Science", "Analyze measured density, tensile response and apparent porosity."),
    "signals": ("Signal Processing", "Smooth, transform and resample measured signals."),
    "fitting": ("Fitting and Inference", "Regression, classical tests, correlations and multiple-comparison adjustments."),
    "uncertainty": ("Measurement Uncertainty", "First-order GUM propagation with correlations and Student t coverage."),
    "numerics": ("Numerical Methods", "Adaptive ODE initial-value solvers, roots, quadrature and cubic splines."),
    "linalg": ("Linear Algebra", "QR, Cholesky, least squares, symmetric eigenvalues, SVD, inverse, determinant and PCA."),
}


# Preserve original pack and operation IDs and saved receipts; picker descriptions
# can evolve as a pack gains operations.
# New packs are discovered from their own plugin.toml and operation module,
# without editing this map. Keep the guide's original inventory pinned to
# these packs, so future packs can be documented independently.
PINNED_PACKS = frozenset(PACKS)


def discover_pack_manifests(
    plugins_root: Path, packs_root: Path,
) -> dict[str, tuple[str, str, str]]:
    """Read *data-only* bundled manifests; never import unapproved pack code.

    A new pack needs packs/<id>.py (OPERATIONS), plugins/science_<id>/
    plugin.py and plugin.toml. Optional [scientific] family = "biology"
    places its picker entry under Biology Workbench. The plugin SDK handles
    manifest validation and the actual grant-gated activation separately.
    """
    found: dict[str, tuple[str, str, str]] = {}
    for manifest_path in sorted(plugins_root.glob("science_*/plugin.toml")):
        pack_id = manifest_path.parent.name.removeprefix("science_")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", pack_id):
            continue
        if not (packs_root / f"{pack_id}.py").is_file():
            continue
        if not (manifest_path.parent / "plugin.py").is_file():
            continue
        try:
            with manifest_path.open("rb") as source:
                document = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError):
            # Invalid plugins also fail the SDK's normal manifest validation.
            continue
        plugin = document.get("plugin")
        if not isinstance(plugin, dict) or plugin.get("id") != f"science_{pack_id}":
            continue
        title, description = plugin.get("name"), plugin.get("description", "")
        if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
            continue
        options = document.get("scientific", {})
        if not isinstance(options, dict):
            continue
        family = options.get("family", "")
        if family not in ("", "biology"):
            continue
        found[pack_id] = (title, description, family)
    return found


PACK_FAMILIES: dict[str, str] = {}
for _pack_id, (_title, _description, _family) in discover_pack_manifests(
    resolve_plugins_dir(), Path(__file__).resolve().parent / "packs",
).items():
    PACKS.setdefault(_pack_id, (_title, _description))
    if _family:
        PACK_FAMILIES[_pack_id] = _family



def operations(pack_id: str) -> tuple[Operation, ...]:
    if pack_id not in PACKS:
        raise ValueError("Unknown scientific pack.")
    base = import_module(f".packs.{pack_id}", __package__).OPERATIONS
    if pack_id == "fitting":
        return base + import_module(".packs.inferential", __package__).OPERATIONS
    return base
