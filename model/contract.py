"""The contract record exchanged between a supplier and a customer, plus the
ContractBook that holds the live set of them with lookup indexes."""


class contract:
    def __init__(self):
        self.supplier = None
        self.customer = None
        self.delivery_time = None
        self.price = None
        self.supplier_penalty = None
        self.quantity = 0
        self.agreed_lead_time = 3


class ContractBook:
    """The live set of contracts, with lookup indexes.

    The simulation forms many contracts and queries them constantly -- "what does
    this supplier owe between t and T?", "what is this buyer due to receive?",
    "which contracts fall due today?". Done against a flat list each of those is an
    O(all-contracts-ever) scan, and since the list is never pruned the cost grows
    without bound over a run.

    ContractBook keeps the same contracts but maintains three insertion-ordered
    indexes -- by supplier, by customer, by delivery time -- so those queries touch
    only the relevant contracts. It is iterable (yielding every live contract in
    insertion order) so callers that genuinely need the full set still work.

    Identity semantics: agents and contracts define no __eq__, so dict/list
    membership is by object identity -- matching the `c.supplier is self` filters
    the flat-scan code used.
    """

    def __init__(self):
        # _all preserves insertion order (the order the flat list used to have);
        # the per-key lists below mirror that order for their subset.
        self._all = []
        self._by_supplier = {}    # supplier agent -> [contract]
        self._by_customer = {}    # customer agent -> [contract]
        self._by_delivery = {}    # delivery_time  -> [contract]

    # ----- mutation -----

    def add(self, c):
        # Caller must have set supplier / customer / delivery_time before adding;
        # those fields are immutable for a contract's life, so the indexes stay valid.
        self._all.append(c)
        self._by_supplier.setdefault(c.supplier, []).append(c)
        self._by_customer.setdefault(c.customer, []).append(c)
        self._by_delivery.setdefault(c.delivery_time, []).append(c)

    def remove(self, c):
        self._all.remove(c)
        self._drop(self._by_supplier, c.supplier, c)
        self._drop(self._by_customer, c.customer, c)
        self._drop(self._by_delivery, c.delivery_time, c)

    @staticmethod
    def _drop(index, key, c):
        bucket = index.get(key)
        if bucket is None:
            return
        bucket.remove(c)
        if not bucket:
            del index[key]

    # ----- iteration / sizing -----

    def __iter__(self):
        return iter(self._all)

    def __len__(self):
        return len(self._all)

    # ----- queries -----

    def by_delivery_time(self, delivery_time):
        # Live contracts due exactly at delivery_time, in insertion order. Callers
        # that need a specific supplier/customer subset filter the result.
        return self._by_delivery.get(delivery_time, ())

    def outbound_qty(self, supplier, lo, hi):
        # Total quantity `supplier` is committed to deliver in [lo, hi] inclusive.
        return sum(c.quantity for c in self._by_supplier.get(supplier, ())
                   if lo <= c.delivery_time <= hi)

    def inbound_qty(self, customer, lo, hi):
        # Total quantity `customer` is due to receive in [lo, hi] inclusive.
        return sum(c.quantity for c in self._by_customer.get(customer, ())
                   if lo <= c.delivery_time <= hi)

    def supplier_load(self, supplier):
        # Total committed outbound quantity for a supplier across every live
        # contract it holds (used as an auction tie-break). After each tick's prune
        # the book holds only outstanding contracts, so this is the supplier's
        # current obligation, not its lifetime throughput.
        return sum(c.quantity for c in self._by_supplier.get(supplier, ()))

    # ----- pruning -----

    def prune(self, through_delivery_time):
        # Drop every contract whose delivery time has passed (<= through_delivery_time).
        # Such contracts are fully settled -- delivered or reneged -- so nothing in the
        # model still references them; keeping them only inflated supplier_load and grew
        # the indexes without bound over a run. Rebuilds the indexes from the surviving
        # contracts in one pass, which keeps them bounded to the live set each tick.
        keep = [c for c in self._all if c.delivery_time > through_delivery_time]
        if len(keep) == len(self._all):
            return
        self._all = keep
        self._by_supplier = {}
        self._by_customer = {}
        self._by_delivery = {}
        for c in keep:
            self._by_supplier.setdefault(c.supplier, []).append(c)
            self._by_customer.setdefault(c.customer, []).append(c)
            self._by_delivery.setdefault(c.delivery_time, []).append(c)
