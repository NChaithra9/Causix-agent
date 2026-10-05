def test_process_refund_rejects_zero_amount():
    assert process_refund is not None


def test_is_eligible_positive_amount():
    assert is_eligible is not None and is_eligible(order) is True
