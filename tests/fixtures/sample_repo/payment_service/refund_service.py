class RefundService:
    def __init__(self, repository):
        self.repository = repository

    def is_eligible_for_refund(self, order):
        profile = self.repository.get_payment_profile(order.customer_id)
        return profile.allows_refunds and order.amount > 0

    def process_refund(self, order):
        if not self.is_eligible_for_refund(order):
            raise ValueError("REFUND_NOT_ALLOWED")
        return self.repository.save_refund(order.id, order.amount)
