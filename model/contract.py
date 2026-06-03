"""The contract record exchanged between a supplier and a customer."""


class contract:
    def __init__(self):
        self.supplier = None
        self.customer = None
        self.delivery_time = None
        self.price = None
        self.supplier_penalty = None
        self.quantity = 0
        self.agreed_lead_time = 3
