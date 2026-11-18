"""Reference and mass/charge-conservation tests for the Chemistry pack.

Sources: OpenStax Chemistry 2e chapters 9, 12, 14; CIAAW 2024; 2022 CODATA.
See docs/SCIENTIFIC_NATURAL_REFERENCE.md for URLs, assumptions and digits.
"""

import math

import pytest

from graphlink_plugins.scientific.core import run_operation
from graphlink_plugins.scientific.packs import chemistry


def _run(identifier, **changes):
    operation = next(o for o in chemistry.OPERATIONS if o.id == identifier)
    return run_operation("chemistry", operation, {**operation.example, **changes})


def test_legacy_operations_ids_defaults_and_values_are_unchanged():
    assert [o.id for o in chemistry.OPERATIONS[:3]] == [
        "beer_lambert", "transmission_spectrum", "isotope_average"]
    assert _run("beer_lambert", absorbance=0.15, molar_absorptivity=2000,
                path_length_cm=0.5)["values"]["concentration_mol_l"] == pytest.approx(0.00015)
    transmission = _run("transmission_spectrum", wavelength_nm=[400, 450, 500],
                        transmission_percent=[100, 10, 1])
    assert transmission["plot"]["y"] == pytest.approx([0, 1, 2])
    assert transmission["values"]["peak_wavelength_nm"] == 500
    assert _run("isotope_average", isotope_mass_u=[10, 12],
                abundance_percent=[25, 75])["values"]["mean_atomic_mass_u"] == 11.5


def test_every_new_field_has_unit_and_examples_run_in_workbench_schema():
    for operation in chemistry.OPERATIONS[3:]:
        assert operation.fields and all(field.unit for field in operation.fields)
        output = _run(operation.id)
        assert output["summary"] and output["values"]
        if "table" in output:
            assert all("(" in title or title in ("Method", "Element") for title in
                       output["table"]["columns"])
        if "plot" in output:
            assert "(" in output["plot"]["xLabel"] and "(" in output["plot"]["yLabel"]


def test_monoprotic_acid_matches_openstax_2_digit_initial_ph():
    # OpenStax 14.7 gives 2.87 using x << 0.1; exact root differs by ~0.0053 pH.
    pka = -math.log10(1.8e-5)
    result = _run("acid_base_speciation", family="acid", pka_values=[pka],
                  formal_concentration_mol_l=0.1, spectator_charge_mol_l=0)
    values = result["values"]
    assert values["ph"] == pytest.approx(2.87, abs=0.01)
    assert values["poh"] == pytest.approx(14 - values["ph"])
    assert sum(row[2] for row in result["table"]["rows"]) == pytest.approx(1)
    assert values["hydrogen_mol_l"] == pytest.approx(
        0.1 * result["table"]["rows"][1][2] + values["hydroxide_mol_l"], rel=1e-10)


def test_monoprotic_base_uses_conjugate_acid_pka():
    # NH4+ conjugate acid pKa 9.25; 0.10 M NH3 yields pH about 11.12 at 25 C.
    values = _run("acid_base_speciation", family="base", pka_values=[9.25],
                  formal_concentration_mol_l=0.1, spectator_charge_mol_l=0)["values"]
    assert values["ph"] == pytest.approx(11.13, abs=0.02)
    assert values["hydroxide_mol_l"] == pytest.approx(
        values["hydrogen_mol_l"] + 0.1 * values["fraction_0"], rel=1e-9)


def test_triprotic_speciation_releases_three_protons_and_respects_charge():
    pka = [2.15, 7.20, 12.35]
    result = _run("acid_base_speciation", family="acid", pka_values=pka,
                  formal_concentration_mol_l=0.1, spectator_charge_mol_l=0)
    rows, values = result["table"]["rows"], result["values"]
    assert len(rows) == 4
    assert sum(r[2] for r in rows) == pytest.approx(1, abs=1e-14)
    assert sum(r[3] for r in rows) == pytest.approx(0.1)
    negative_species = -sum(r[1] * r[3] for r in rows)
    assert negative_species == pytest.approx(values["hydrogen_mol_l"] - values["hydroxide_mol_l"], rel=1e-10)
    # An isoelectric/salt form can be represented by a fixed spectator charge.
    salt = _run("acid_base_speciation", family="acid", pka_values=pka,
                formal_concentration_mol_l=0.1, spectator_charge_mol_l=0.1)["values"]
    assert 3 < salt["ph"] < 7.2


def test_buffer_hh_exact_and_capacity_with_openstax_reference():
    pka = -math.log10(1.8e-5)
    result = _run("buffer_ph_capacity", pka=pka,
                  acid_concentration_mol_l=0.1, base_concentration_mol_l=0.1)["values"]
    # OpenStax 14.6 0.10 M acetate/acid buffer: pH 4.74 (to 0.01).
    assert result["ph_henderson_hasselbalch"] == pytest.approx(4.74, abs=0.005)
    assert result["ph_charge_balance"] == pytest.approx(4.74, abs=0.005)
    assert result["buffer_capacity_hh_mol_l_per_ph"] == pytest.approx(math.log(10) * 0.05)
    assert result["buffer_capacity_exact_mol_l_per_ph"] > 0
    assert abs(result["ph_exact_minus_hh"]) < 0.001
    # Dilute buffers require explicit water charge balance, not just HH.
    dilute = _run("buffer_ph_capacity", pka=pka,
                  acid_concentration_mol_l=1e-8, base_concentration_mol_l=1e-8)["values"]
    assert abs(dilute["ph_exact_minus_hh"]) > 0.1


def test_titration_reproduces_openstax_weak_acid_table_and_marks_equivalence():
    # OpenStax 14.7: 25.00 mL 0.100 M CH3COOH, 0.100 M NaOH.
    result = _run("titration_curve", family="acid", pka_values=[-math.log10(1.8e-5)],
                  analyte_concentration_mol_l=0.1, analyte_volume_ml=25,
                  titrant_concentration_mol_l=0.1, max_titrant_volume_ml=50, samples=101)
    val = result["values"]
    assert val["equivalence_1_volume_ml"] == 25
    assert val["initial_ph"] == pytest.approx(2.87, abs=0.01)
    assert val["half_equivalence_1_ph"] == pytest.approx(4.74, abs=0.01)
    assert val["equivalence_1_ph"] == pytest.approx(8.72, abs=0.005)
    assert next(ph for v, ph in result["table"]["rows"] if v == 37.5) == pytest.approx(12.30, abs=0.005)
    assert result["plot"]["x"] == sorted(result["plot"]["x"])


def test_polyprotic_equivalences_and_base_titration_direction():
    acid = _run("titration_curve", family="acid", pka_values=[2.15, 7.2, 12.35],
                max_titrant_volume_ml=90, samples=27)["values"]
    assert [acid[f"equivalence_{i}_volume_ml"] for i in range(1, 4)] == pytest.approx([25, 50, 75])
    assert acid["initial_ph"] < acid["final_ph"]
    base = _run("titration_curve", family="base", pka_values=[9.25],
                max_titrant_volume_ml=50, samples=11)
    assert base["values"]["initial_ph"] > base["values"]["final_ph"]
    assert any(abs(v - 25) < 1e-12 for v in base["plot"]["x"])


def test_arrhenius_openstax_published_regression_digits_and_uncertainty():
    # OpenStax 12.5 graphs ~180 kJ/mol after rounding a two-point slope;
    # fitting all five *rounded* tabulated pairs gives approximately 186 kJ/mol.
    inv_temp = [1.80e-3, 1.74e-3, 1.55e-3, 1.43e-3, 1.28e-3]
    log_k = [-14.860, -13.617, -9.362, -6.759, -3.231]
    result = _run("arrhenius_fit", temperature_k=[1 / x for x in inv_temp],
                  rate_constant=[math.exp(y) for y in log_k], rate_constant_unit="s^-1")["values"]
    assert result["activation_energy_kj_mol"] == pytest.approx(180, abs=10)
    assert result["activation_energy_standard_error_kj_mol"] > 0
    assert result["residual_degrees_of_freedom"] == 3
    assert result["r_squared"] > 0.999
    # A perfectly log-linear Arrhenius dataset has nearly zero slope uncertainty.
    ea, a = 52_000, 1e7
    temperatures = [280, 300, 325, 350]
    fitted = _run("arrhenius_fit", temperature_k=temperatures,
                  rate_constant=[a * math.exp(-ea / (8.31446261815324 * t)) for t in temperatures],
                  rate_constant_unit="L/(mol·s)")["values"]
    assert fitted["activation_energy_kj_mol"] == pytest.approx(52, rel=1e-12)
    assert fitted["prefactor"] == pytest.approx(a, rel=1e-12)
    assert fitted["activation_energy_standard_error_kj_mol"] < 1e-10


@pytest.mark.parametrize("order,k,c0", [("first", 0.02, 0.2), ("second", 0.3, 0.2)])
def test_integrated_rate_laws_match_openstax_equations(order, k, c0):
    # OpenStax 12 key equations: lnC=lnC0-kt; 1/C=1/C0+kt.
    times = [0, 10, 30, 60, 90]
    c = [c0 * math.exp(-k * t) if order == "first" else 1 / (1 / c0 + k * t)
         for t in times]
    output = _run("rate_law_fit", order=order, time_s=times, concentration_mol_l=c)
    values = output["values"]
    assert values["rate_constant_per_s" if order == "first" else "rate_constant_l_mol_s"] == pytest.approx(k)
    assert values["initial_concentration_mol_l"] == pytest.approx(c0)
    assert values["half_life_s"] == pytest.approx(math.log(2) / k if order == "first" else 1 / (k * c0))
    assert values["r_squared_transformed"] == pytest.approx(1)
    assert max(abs(r[-1]) for r in output["table"]["rows"]) < 1e-12


def test_ideal_gas_codata_molar_volume_at_one_atmosphere_and_vdw_consistency():
    # CODATA 2022: 22.41396954 L/mol at 273.15 K and 101325 Pa.
    pressure = _run("gas_pressure", amount_mol=1, temperature_k=273.15,
                    volume_l=22.41396954, a_l2_bar_mol2=0, b_l_mol=0)["values"]
    assert pressure["ideal_pressure_bar"] == pytest.approx(1.01325, rel=1e-9)
    assert pressure["van_der_waals_pressure_bar"] == pytest.approx(1.01325, rel=1e-9)
    # OpenStax 9.6 CO2 a=3.59 L² atm/mol², b=0.0427 L/mol.
    params = dict(amount_mol=1, temperature_k=300,
                  a_l2_bar_mol2=3.59 * 1.01325, b_l_mol=0.0427)
    result = _run("gas_pressure", volume_l=25, **params)["values"]
    vdw_formula = 0.0831446261815324 * 300 / (25 - 0.0427) - (3.59 * 1.01325) / 625
    assert result["van_der_waals_pressure_bar"] == pytest.approx(vdw_formula, rel=1e-12)
    volume = _run("gas_volume", pressure_bar=result["van_der_waals_pressure_bar"], **params)["values"]
    assert volume["van_der_waals_gas_volume_l"] == pytest.approx(25, rel=1e-9)
    ideal = _run("gas_volume", pressure_bar=1.01325, amount_mol=1, temperature_k=273.15,
                 a_l2_bar_mol2=0, b_l_mol=0)["values"]
    assert ideal["ideal_volume_l"] == pytest.approx(22.41396954, rel=1e-9)


@pytest.mark.parametrize("formula,expected", [
    ("H2O", 18.015), ("Ca(OH)2", 74.092), ("Al2(SO4)3", 342.132),
    ("CuSO4·5H2O", 249.677), ("CuSO4.5H2O", 249.677),
    ("K4[Fe(CN)6]", 368.346),
])
def test_formula_molar_mass_2024_ciaaw_to_published_precision(formula, expected):
    # The CIAAW 2024 *abridged* atomic weights are used, not isotope masses.
    values = _run("molar_mass", formula=formula)["values"]
    assert values["molar_mass_g_mol"] == pytest.approx(expected, abs=0.001)


def test_dilution_and_limiting_reagent_balance():
    dilution = _run("solution_dilution", stock_concentration_mol_l=2,
                    aliquot_volume_ml=10, final_volume_ml=100)["values"]
    assert dilution["final_concentration_mol_l"] == pytest.approx(0.2)
    assert dilution["dilution_factor"] == 10
    assert dilution["solute_amount_mmol"] == 20
    # 2 H2 + O2 -> 2 H2O; 4.032 g H2=2 mol, 16 g O2=0.5 mol:
    # O2 limits; 1 mol H2O = 18.015 g, product measured 16.2135 g = 90%.
    values = _run("reaction_yield", reactant_coefficients=[2, 1],
                  reactant_mass_g=[4.032, 15.999], reactant_molar_mass_g_mol=[2.016, 31.998],
                  product_coefficient=2, product_molar_mass_g_mol=18.015,
                  actual_product_mass_g=16.2135)["values"]
    assert values["limiting_reactant_index"] == 2
    assert values["reaction_extent_mol"] == pytest.approx(0.5)
    assert values["theoretical_product_mass_g"] == pytest.approx(18.015)
    assert values["percent_yield"] == pytest.approx(90)


@pytest.mark.parametrize("op,updates", [
    ("acid_base_speciation", {"family": "salt"}),
    ("acid_base_speciation", {"pka_values": [4, 3]}),
    ("acid_base_speciation", {"formal_concentration_mol_l": -1}),
    ("buffer_ph_capacity", {"acid_concentration_mol_l": 0}),
    ("titration_curve", {"max_titrant_volume_ml": 10}),
    ("titration_curve", {"samples": 2}),
    ("arrhenius_fit", {"temperature_k": [250, 300]}),
    ("arrhenius_fit", {"rate_constant": [0, 1, 2, 3]}),
    ("rate_law_fit", {"time_s": [1, 1, 2, 3]}),
    ("rate_law_fit", {"concentration_mol_l": [1, 0, 1, 1]}),
    ("gas_pressure", {"volume_l": 0.01}),
    ("gas_volume", {"pressure_bar": 0}),
    ("molar_mass", {"formula": "CuSO4·0H2O"}),
    ("molar_mass", {"formula": "Ca(OH2"}),
    ("molar_mass", {"formula": "Xx2"}),
    ("molar_mass", {"formula": "H2O)"}),
    ("molar_mass", {"formula": "TcO2"}),
    ("solution_dilution", {"final_volume_ml": 1}),
    ("reaction_yield", {"reactant_coefficients": [1, 0]}),
    ("reaction_yield", {"actual_product_mass_g": -1}),
])
def test_rejects_invalid_or_unphysical_inputs(op, updates):
    with pytest.raises(ValueError):
        _run(op, **updates)


def test_exact_buffer_capacity_matches_numerical_strong_base_derivative():
    pk = 6.4
    acid, base = 0.02, 0.05
    value = _run("buffer_ph_capacity", pka=pk, acid_concentration_mol_l=acid,
                 base_concentration_mol_l=base)["values"]
    eps = 1e-6  # mol/L of strong base at constant total weak buffer concentration
    plus = _run("acid_base_speciation", family="acid", pka_values=[pk],
                formal_concentration_mol_l=acid + base, spectator_charge_mol_l=base + eps)["values"]["ph"]
    minus = _run("acid_base_speciation", family="acid", pka_values=[pk],
                 formal_concentration_mol_l=acid + base, spectator_charge_mol_l=base - eps)["values"]["ph"]
    numerical = 2 * eps / (plus - minus)
    assert value["buffer_capacity_exact_mol_l_per_ph"] == pytest.approx(numerical, rel=1e-8)
