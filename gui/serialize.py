"""Serialise a Simulation's current state into a JSON-able snapshot for the GUI.

Called once per tick by the runner. Carries everything the frontend needs:
per-agent metrics (for dot sizing), the active contract edges (for drawing the
map), this tick's events (for animations + the graph) and the captured stdout
text (for the readout panel).
"""

import statistics


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
    # The model prunes settled contracts at the end of each tick, so the book holds
    # only live (delivery due now or later) contracts by the time we snapshot. The
    # delivery_time >= sim.t filter is kept as a defensive belt-and-braces guard.
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

    # Bump-probability beliefs: one curve per directed (buyer <- supplier) pair.
    # Each buyer holds a fitted logistic (w0, w_lead, w_stake) about each upstream
    # supplier. The frontend draws
    #     P(bump) = sigmoid(a + b * lead_frac + c * stake)
    # evaluated at the pair's MEDIAN observed stake (price + penalty). Holding the
    # stake term at a value the data actually saw -- rather than dropping it / using
    # stake = 0 -- keeps the curve in-distribution: the intercept and stake term are
    # strongly anti-correlated in the fit, so zeroing stake makes the bare intercept
    # blow the curve up to ~1 and lets lead/stake confounding flip the slope.
    bump_curves = []
    for buyer in sim.all_agents:
        for supplier in getattr(buyer, "upstream_suppliers", None) or []:
            w0, w_lead, w_stake = buyer.model_params(supplier)
            obs = buyer.observations.get(supplier, [])
            stake_med = statistics.median([o[1] for o in obs]) if obs else 0.0
            bump_curves.append({
                "label": f"{buyer.name} ← {supplier.name}",
                "a": float(w0),
                "b": float(w_lead),
                "c": float(w_stake),
                "stake": float(stake_med),
            })

    return {
        "t": sim.t,
        "done": sim.t > sim.simulation_length,
        "tiers": tiers,
        "edges": edges,
        "events": list(sim.events),
        "bump_curves": bump_curves,
        "log": log_text,
    }
