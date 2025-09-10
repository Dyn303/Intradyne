from app.compliance import assert_whitelisted, enforce_spot_only, forbid_shorting, ComplianceError


def test_whitelist_blocks_non_whitelisted():
    wl = ["BTC/USDT", "ETH/USDT"]
    try:
        assert_whitelisted("XRP/USDT", wl)
    except ComplianceError:
        pass
    else:
        assert False, "Expected ComplianceError for non-whitelisted symbol"


def test_spot_only_enforcement():
    try:
        enforce_spot_only({"leverage": 5})
    except ComplianceError:
        pass
    else:
        assert False, "Expected ComplianceError for leverage"


def test_forbid_shorting_when_no_inventory():
    try:
        forbid_shorting("sell", 0.0)
    except ComplianceError:
        pass
    else:
        assert False, "Expected ComplianceError for shorting"

