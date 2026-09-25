class OrderService:
    def place_order(self, cart):
        return {"items": cart.items, "status": "PLACED"}

    def cancel_order(self, order):
        order.status = "CANCELLED"
        return order
