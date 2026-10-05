def is_eligible(order):
    return order.amount > 0


def process_refund(order):
    if not is_eligible(order):
        raise ValueError("REFUND_NOT_ALLOWED")
    return {"order": order.id, "status": "REFUNDED"}


def refund_endpoint(request):
    return process_refund(request.order)


def unrelated_report():
    return "monthly report"
