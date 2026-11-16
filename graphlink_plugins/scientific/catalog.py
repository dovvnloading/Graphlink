"""Bundled scientific packs; domain modules are loaded on grant."""

from importlib import import_module

from .core import Operation

PACKS = {
    "core": ("Scientific Core", "Import numeric CSV, convert measurement units and plot paired observations."),
    "mathematics": ("Mathematics", "Evaluate polynomials, integrate samples and solve linear systems."),
    "statistics": ("Statistics", "Describe samples, fit a regression line and summarize weighted observations."),
    "physics": ("Physics", "Model ideal oscillation, steady heat flow and calorimetry."),
    "astronomy": ("Astronomy", "Estimate stellar distances and blackbody peak wavelengths."),
    "engineering": ("Engineering", "Evaluate resistor networks, RC step response and thermal expansion."),
    "chemistry": ("Chemistry", "Analyze absorption measurements, transmission spectra and isotope abundances."),
    "biology": ("Biology and Ecology", "Compare biological communities, diversity and quadrat survey density."),
    "earth": ("Earth Science", "Analyze spherical distances, geothermal gradients and seismic arrival times."),
    "environment": ("Environmental Science", "Calculate flow-weighted concentrations, runoff and growing degree days."),
    "materials": ("Materials Science", "Analyze measured density, tensile response and apparent porosity."),
    "signals": ("Signal Processing", "Smooth, transform and resample measured signals."),
}


def operations(pack_id: str) -> tuple[Operation, ...]:
    if pack_id not in PACKS:
        raise ValueError("Unknown scientific pack.")
    return import_module(f".packs.{pack_id}", __package__).OPERATIONS
