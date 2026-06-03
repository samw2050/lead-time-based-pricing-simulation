"""Tier: a horizontal layer of agents in the chain."""


class Tier:
    """A horizontal layer of agents in the chain.

    role:
      "producer"     -- top of chain. No upstream tier expected above.
      "intermediary" -- middle. Agents draw inputs from the tier directly above.
      "retailer"     -- bottom. Serves end customers via each agent's demand_fn
                        instead of shipping to a downstream tier.

    Tiers live in chain order in Simulation.tiers (top-of-chain first). Upstream
    wiring (agent.upstream_suppliers) is derived from that order at Simulation
    construction. At most one tier may be 'retailer' and it must be last.
    """
    _VALID_ROLES = ("producer", "intermediary", "retailer")

    def __init__(self, name, role="intermediary", agents=None):
        if role not in self._VALID_ROLES:
            raise ValueError(f"role must be one of {self._VALID_ROLES}, got {role!r}")
        self.name = name
        self.role = role
        self.agents = list(agents or [])

    def add(self, a):
        self.agents.append(a)
        return a
