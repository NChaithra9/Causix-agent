def generate_invoice(order):
    return {"order_id": order.id, "total": order.amount, "currency": "INR"}


def send_invoice_email(invoice, mailer):
    mailer.send(to=invoice["email"], subject="Your invoice", body=str(invoice))
