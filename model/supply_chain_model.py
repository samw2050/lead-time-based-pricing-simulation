"""Demo entry point: a 3-tier supply-chain scenario.

Run this file directly to drive the simulation end to end. To build a different
scenario, construct Tier objects (top-of-chain first) and pass them to
Simulation -- the building blocks live in the sibling modules:

    schedules.py   supply / demand / revenue function factories
    bump_model.py  logistic bump-probability model
    solver.py      two-stage contract solver
    contract.py    the contract record
    tier.py        Tier: a horizontal layer of agents
    agent.py       agent: a single firm in the chain
    simulation.py  Simulation: the tick loop and auction
"""

from agent import agent
from schedules import fixed, linear, sinusoidal
from simulation import Simulation
from tier import Tier


if __name__ == "__main__":
    # tier2: top -- raw producers. supply_fn is start-capacity per tick (units it
    # can begin producing this tick). production_time=2 means a unit started at t
    # completes at t+2 and is available for delivery from t+2 onward.
    # penalty_scale: how heavily each supplier weights a bump penalty in its OWN
    # decision to abandon an existing customer (the bumped customer always receives
    # the unscaled contractual penalty). >1 makes the supplier stickier to existing
    # commitments -- e.g. penalty_scale=2 means it only bumps when the new offer beats
    # price + penalty*2 rather than price + penalty.
    tier2 = Tier("tier2", role="producer", agents=[
        agent(name="tier2_1", cost=50, supply_fn=linear(start=50, slope=0, floor=0),
              production_time=2, inventory=50, safety_stock=50, penalty_scale=1),
        agent(name="tier2_2", cost=50, supply_fn=linear(start=50, slope=0, floor=0),
              production_time=2, inventory=50, safety_stock=50, penalty_scale=0),
    ])

    # tier1: middle intermediaries. Their own assembly capacity (supply_fn) plus
    # required inputs from tier2 (input_inventory enables the second pool). cost /
    # production_cost split: `cost` is the all-in optimizer basis (Phase-1
    # placeholder), `production_cost` is the value-add used in balance accounting.
    # revenue_forecast_fn is the resale price tier1 expects (Phase-1 placeholder;
    # Phase 3 will derive it). Seeded with both finished inventory (so it can sell
    # at lead 0 on tick 0) and input_inventory (so it can start assembly on tick 0
    # without waiting for tier2).
    tier1 = Tier("tier1", role="intermediary", agents=[
        agent(name="tier1_1", cost=60, production_cost=12,
              supply_fn=linear(start=50, slope=0, floor=0),
              production_time=2, inventory=50, input_inventory=50,
              safety_stock=50, revenue_forecast_fn=fixed(100), penalty_scale=1.1),
        agent(name="tier1_2", cost=60, production_cost=12,
              supply_fn=linear(start=50, slope=0, floor=0),
              production_time=2, inventory=50, input_inventory=50,
              safety_stock=50, revenue_forecast_fn=fixed(100), penalty_scale=1.1),
    ])

    # oem: bottom -- end-customer-facing pure retailers. production_time=0 (no
    # assembly), no input_inventory -- inbound deliveries land directly in inventory.
    oem = Tier("oem", role="retailer", agents=[
        agent(name="oem_1", demand_fn=sinusoidal(50,10,6),
              revenue_fn=linear(130,1), revenue_forecast_fn=linear(130,1),
              safety_stock=5, inventory=5),
        agent(name="oem_2", demand_fn=sinusoidal(80,15,6,90),
              revenue_fn=linear(120,1), revenue_forecast_fn=linear(120,1),
              safety_stock=20, inventory=20),
        agent(name="oem_3", demand_fn=sinusoidal(20,3,6,180),
              revenue_fn=linear(140,1), revenue_forecast_fn=linear(140,1),
              safety_stock=2, inventory=2),
    ])

    Simulation(tiers=[tier2, tier1, oem], forecast_window=12, simulation_length=13).run()
