import pytest

from bot import auto_select_strike


def test_atm_nifty_rounds_to_nearest_50():
    assert auto_select_strike(22432.45, "CE", step=50, depth=0) == 22450
    assert auto_select_strike(22432.45, "PE", step=50, depth=0) == 22450


def test_one_strike_itm_call_and_put():
    spot = 22432.45
    assert auto_select_strike(spot, "CE", step=50, depth=1) == 22400
    assert auto_select_strike(spot, "PE", step=50, depth=1) == 22500


def test_one_strike_otm_call_and_put():
    spot = 22432.45
    assert auto_select_strike(spot, "CE", step=50, depth=-1) == 22500
    assert auto_select_strike(spot, "PE", step=50, depth=-1) == 22400


def test_banknifty_step_100():
    assert auto_select_strike(48120, "CE", step=100, depth=0) == 48100
    assert auto_select_strike(48120, "CE", step=100, depth=1) == 48000


def test_invalid_option_type():
    with pytest.raises(ValueError, match="Invalid option_type"):
        auto_select_strike(22400, "XX")


def test_invalid_step():
    with pytest.raises(ValueError, match="step must be"):
        auto_select_strike(22400, "CE", step=0)
