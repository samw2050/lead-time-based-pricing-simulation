"""Serialise a Simulation's current state into a JSON-able snapshot for the GUI.

Called once per tick by the runner. Carries everything the frontend needs:
per-agent metrics (for dot sizing), the active contract edges (for drawing the
map), this tick's events (for animations + the graph) and the captured stdout
text (for the readout panel).
"""


def _agent_state(a):
    wip = sum(a.production_schedule.values()) if a.production_schedule else 0
    n_contracts = 0  # filled in by snapshot() which can see the contract list
    return {
        "name": a.name,
        "inventory": round(float(a.inventory), 2),
        "input_inventory": (None if a.input_inventory is None
                            else round(float(a.input_inventory), 2)),
        "wip": round(float(wip), 2),
        "balance": round(float(a.balance), 2),
        "contracts": n_contracts,
    }


def snapshot(sim, log_text=""):
    # The model never prunes delivered contracts from sim.contracts, so only the
    # still-open ones (delivery due now or later) represent live relationships.
    active = [c for c in sim.contracts if c.delivery_time >= sim.t]

    # Count active contracts per agent (as supplier or customer) for the
    # "contracts" dot-size metric.
    contract_count = {}
    for c in active:
        contract_count[c.supplier.name] = contract_count.get(c.supplier.name, 0) + 1
        contract_count[c.customer.name] = contract_count.get(c.customer.name, 0) + 1

    tiers = []
    for tier in sim.tiers:
        agents = []
        for a in tier.agents:
            state = _agent_state(a)
            state["contracts"] = contract_count.get(a.name, 0)
            agents.append(state)
        tiers.append({"name": tier.name, "role": tier.role, "agents": agents})

    # Collapse active contracts to one edge per supplier->customer pair, summing
    # quantity, so the map draws a handful of lines rather than thousands.
    edge_map = {}
    for c in active:
        key = (c.supplier.name, c.customer.name)
        e = edge_map.setdefault(key, {"supplier": c.supplier.name,
                                      "customer": c.customer.name, "qty": 0.0, "count": 0})
        e["qty"] += float(c.quantity)
        e["count"] += 1
    edges = [{**e, "qty": round(e["qty"], 2)} for e in edge_map.values()]

    return {
        "t": sim.t,
        "done": sim.t > sim.simulation_length,
        "tiers": tiers,
        "edges": edges,
        "events": list(sim.events),
        "log": log_text,
    }
