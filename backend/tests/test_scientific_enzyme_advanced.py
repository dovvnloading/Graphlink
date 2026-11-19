"""Independent numerical oracles for the advanced enzyme-kinetics operations."""

import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import readable_result, run_operation
from graphlink_plugins.scientific.packs import enzyme_advanced


PACK = "enzyme_kinetics"


def _run(name, **kwargs):
    op = next(op for op in operations(PACK) if op.id == name)
    return run_operation(PACK, op, kwargs)


def _binding(model):
    ligand = [0, .01, .03, .07, .1, .2, .4, .8, 1.5, 3, 5, 8, 12, 20, 30, 60, 100, 200]
    baseline = 4.0
    if model == "one_site":
        response = [baseline + 70 * x / (2 + x) for x in ligand]
    elif model == "one_site_nonspecific":
        response = [baseline + 90 * x / (1.4 + x) + 1.7 * x for x in ligand]
    else:
        response = [baseline + 40 * x / (.3 + x) + 70 * x / (12 + x) for x in ligand]
    return {"ligand": ligand,
            "response": [v + (0.003 if i % 2 else -0.003) for i, v in enumerate(response)],
            "concentration_unit": "uM", "response_unit": "RFU", "model": model}


@pytest.mark.parametrize("model", ("one_site", "one_site_nonspecific", "two_site"))
def test_binding_isotherms_recover_independent_parameters(model):
    data = _binding(model)
    result = _run("binding_isotherm_fit", **data)
    v = result["values"]
    assert v["model"] == model
    assert v["baseline"] == pytest.approx(4, abs=.1)
    assert v["r_squared"] > .99999
    assert v["converged"] == "true"
    assert len(result["table"]["rows"]) == len(data["ligand"])
    assert result["plot"]["x"] == data["ligand"]
    assert all(row[3] == pytest.approx(row[1] - row[2]) for row in result["table"]["rows"])
    if model == "one_site":
        assert v["kd"] == pytest.approx(2, rel=.03)
        assert v["bmax"] == pytest.approx(70, rel=.02)
        assert v["kd_unit"] == "uM"
        assert v["kd_standard_error"] > 0
    elif model == "one_site_nonspecific":
        assert v["kd"] == pytest.approx(1.4, rel=.03)
        assert v["bmax"] == pytest.approx(90, rel=.02)
        assert v["nonspecific_slope"] == pytest.approx(1.7, rel=.01)
        assert v["nonspecific_slope_unit"] == "RFU/uM"
    else:
        assert v["kd1"] == pytest.approx(.3, rel=.04)
        assert v["kd2"] == pytest.approx(12, rel=.04)
        assert v["bmax1"] == pytest.approx(40, rel=.03)
        assert v["bmax2"] == pytest.approx(70, rel=.03)
        assert v["kd1"] < v["kd2"]
        assert v["kd1_standard_error"] > 0


def test_binding_decreasing_signal_replicates_and_explicit_unit_conversion():
    data = _binding("one_site")
    data["response"] = [150 - value for value in data["response"]]
    data["ligand"] = [f"{x * 1000:g} nM" for x in data["ligand"]]
    result = _run("binding_isotherm_fit", **data)
    v = result["values"]
    assert v["bmax"] == pytest.approx(-70, rel=.02)
    assert v["kd"] == pytest.approx(2, rel=.03)
    assert v["kd_unit"] == "uM"
    assert result["inputs"]["ligand"][1] == pytest.approx(.01)


def _morrison_data():
    inhibitor = [0, .01, .03, .06, .1, .2, .4, .8, 1.2, 2, 4, 8]
    total_enzyme, ki, top, bottom = .5, .08, 100, 5
    # Independent quadratic oracle: enzyme-bound inhibitor/Etotal.
    bound = [(total_enzyme + x + ki - math.sqrt((total_enzyme + x + ki) ** 2 -
              4 * total_enzyme * x)) / (2 * total_enzyme) for x in inhibitor]
    activity = [bottom + (top - bottom) * (1 - fraction) + .002 * (-1) ** i
                for i, fraction in enumerate(bound)]
    return {"inhibitor": inhibitor, "activity": activity, "enzyme_concentration": total_enzyme,
            "concentration_unit": "uM", "response_unit": "uM/s"}


def test_morrison_uses_total_enzyme_and_depletion_quadratic():
    result = _run("morrison_tight_binding_fit", **_morrison_data())
    v = result["values"]
    assert v["ki_app"] == pytest.approx(.08, rel=.02)
    assert v["top"] == pytest.approx(100, rel=.001)
    assert v["bottom"] == pytest.approx(5, rel=.005)
    assert v["ki_app_standard_error"] > 0
    assert v["enzyme_concentration"] == pytest.approx(.5)
    assert v["ki_app_unit"] == "uM"
    assert v["r_squared"] > .9999
    assert result["table"]["rows"][0][2] == pytest.approx(v["top"])


def test_morrison_typed_enzyme_and_inhibitor_units():
    data = _morrison_data()
    data["enzyme_concentration"] = "500 nM"
    data["inhibitor"] = [f"{value * 1000:g} nM" for value in data["inhibitor"]]
    v = _run("morrison_tight_binding_fit", **data)["values"]
    assert v["ki_app"] == pytest.approx(.08, rel=.02)
    assert v["enzyme_concentration"] == pytest.approx(.5)


@pytest.mark.parametrize("slope", (1, 1.45))
def test_schild_recovers_pA2_and_free_slope(slope):
    antagonist = [.001, .003, .01, .03, .1, .3, 1, 3]
    ratios = [1 + (concentration / .01) ** slope for concentration in antagonist]
    data = {"antagonist": antagonist, "dose_ratio": ratios, "concentration_unit": "uM"}
    result = _run("schild_analysis", **data)
    v = result["values"]
    assert v["schild_slope"] == pytest.approx(slope, abs=1e-10)
    assert v["pa2"] == pytest.approx(8, abs=1e-10)  # .01 uM is 1e-8 M.
    assert v["r_squared"] == pytest.approx(1)
    assert v["pa2_unit"].startswith("1 (dimensionless")
    assert len(result["table"]["rows"]) == len(antagonist)
    if slope > 1:
        assert "differs from unity" in v["warnings"]
    else:
        assert "differs from unity" not in v["warnings"]


def test_schild_uses_molar_scale_for_all_compatible_concentration_units():
    antagonist_nm = [1, 3, 10, 30, 100, 300, 1000]
    ratios = [1 + value / 10 for value in antagonist_nm]
    v = _run("schild_analysis", antagonist=antagonist_nm, dose_ratio=ratios,
             concentration_unit="nM")["values"]
    assert v["pa2"] == pytest.approx(8)
    assert v["schild_slope"] == pytest.approx(1)


def _thermal_data(*, decreasing=False):
    temperature = [30, 35, 40, 45, 48, 50, 52, 54, 56, 58, 60, 62, 65, 70, 75, 80]
    curve = [100 + 650 / (1 + math.exp((58.5 - value) / 3.2)) + .05 * (-1) ** i
             for i, value in enumerate(temperature)]
    return {"temperature": temperature,
            "signal": [900 - value for value in curve] if decreasing else curve,
            "temperature_unit": "°C", "response_unit": "RFU"}


@pytest.mark.parametrize("decreasing", (False, True))
def test_thermal_shift_tm_and_width_recovery(decreasing):
    result = _run("thermal_shift_tm", **_thermal_data(decreasing=decreasing))
    v = result["values"]
    assert v["tm"] == pytest.approx(58.5, abs=.1)
    assert v["transition_width"] == pytest.approx(3.2, rel=.03)
    assert v["transition_width_unit"] == "delta_degC"
    assert v["direction"] == ("decreasing" if decreasing else "increasing")
    assert v["r_squared"] > .9999
    assert isinstance(v["tm_standard_error"], float)
    assert result["table"]["rows"][0][0] == 30


def test_thermal_kelvin_and_explicit_celsius_unit_conversion():
    data = _thermal_data()
    data["temperature_unit"] = "K"
    data["temperature"] = [f"{t} °C" for t in data["temperature"]]
    v = _run("thermal_shift_tm", **data)["values"]
    assert v["tm"] == pytest.approx(331.65, abs=.1)
    assert v["tm_unit"] == "K"
    assert v["transition_width"] == pytest.approx(3.2, rel=.03)
    assert v["transition_width_unit"] == "delta_K"


@pytest.mark.parametrize("op_name,changes,match", [
    ("binding_isotherm_fit", {"ligand": [0] * 18}, "distinct"),
    ("binding_isotherm_fit", {"response": [1, 2, 3]}, "aligned"),
    ("binding_isotherm_fit", {"concentration_unit": "s"}, "molarity"),
    ("binding_isotherm_fit", {"model": "three_site"}, "must be one of"),
    ("morrison_tight_binding_fit", {"enzyme_concentration": 0}, "positive"),
    ("morrison_tight_binding_fit", {"inhibitor": [.1] * 12}, "distinct"),
    ("morrison_tight_binding_fit", {"activity": [-1, 99, 96, 92, 87, 76, 60, 41, 30, 20, 12, 7]}, "nonnegative"),
    ("schild_analysis", {"dose_ratio": [1, 1.3, 2, 4, 11, 31, 101, 301]}, "greater than one"),
    ("schild_analysis", {"antagonist": [0] * 8}, "positive"),
    ("schild_analysis", {"concentration_unit": "s"}, "molarity"),
    ("thermal_shift_tm", {"temperature": [30] * 16}, "distinct"),
    ("thermal_shift_tm", {"signal": [1] * 16}, "varying"),
    ("thermal_shift_tm", {"temperature_unit": "uM"}, "temperature"),
])
def test_advanced_inputs_reject_invalid_experimental_designs(op_name, changes, match):
    example = next(op.example for op in operations(PACK) if op.id == op_name)
    with pytest.raises(ValueError, match=match):
        _run(op_name, **{**example, **changes})


def test_new_examples_have_methods_citations_and_verified_result_receipts():
    for op in enzyme_advanced.OPERATIONS:
        assert op.methods and op.citations and all(field.unit for field in op.fields)
        output = _run(op.id, **op.example)
        assert output["provenance"]["operationId"] == op.id
        assert readable_result({"version": 1, "latest": output}, PACK)
        assert len(output["plot"]["x"]) == len(output["table"]["rows"])
        assert output["values"]["r_squared"] > .9
