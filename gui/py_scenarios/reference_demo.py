"""Reference demo: a full 3-tier supply chain exercising every model mechanic.

This is a worked starting point for building your own simulation. It is a Python
scenario (not JSON), so it builds the Simulation in code and can use anything the
model exposes -- including a hand-written list of demand values, which the form
builder can't express. Load it from the GUI's "Python modules" group, or run it
headless with ``python reference_demo.py``.

THE CONTRACT
------------
The GUI requires one thing: a module-level ``build()`` that returns an UN-RUN
Simulation. The runner advances the ticks and serialises each snapshot itself --
so do NOT call ``.run()`` inside build() (the __main__ block at the bottom is the
only place that runs it, for standalone use).

THE CHAIN (top of chain first)
------------------------------
    raw  (producer)     makes raw components from nothing (no upstream tier)
      |  delivers into the assembler's input pool
    asm  (intermediary) consumes inputs + value-adds into finished goods
      |  delivers into the retailer's finished stock
    shop (retailer)     serves exogenous end-customer demand; no downstream tier

MECHANICS ON SHOW (where to look below)
---------------------------------------
  * supply_fn          start capacity per tick (units you can BEGIN making)
  * production_time    lead tau: a unit started at t is finished at t+tau
  * inventory          starting finished stock
  * input_inventory    raw-input pool (intermediaries that assemble only)
  * safety_stock       build-to-stock target the agent tries to keep on hand
  * cost/production_cost  all-in optimiser basis vs the value-add for accounting
  * revenue_forecast_fn   the price a seller expects to resell at (drives bids)
  * revenue_fn         the price a retailer actually earns from end customers
  * penalty_scale      how reluctant a seller is to bump an existing customer
  * shelf_life         PERISHABILITY: ticks a unit may sit in stock before it
                       spoils and is written off (omit = never spoils)
  * demand as a LIST   shop_A's demand is an explicit per-tick path with a sharp
                       jump partway through (see DEMAND_RAMP) -- a demand SHOCK
  * learning           bump-probability models + procurement-price EWMAs are
                       built automatically from realised deliveries each tick;
                       nothing to configure, but obs_window caps the memory

THE DEMAND SHOCK (the teaching centrepiece)
-------------------------------------------
shop_A's demand sits flat at 20, then jumps to 60 at t=5 and stays there. Note a
subtlety worth understanding: a retailer's demand FORECAST is myopic -- it
projects today's demand flat across the planning window (see agent.predict_demand)
-- so the chain does NOT see the jump coming. The spike therefore arrives as a
surprise: expect short-lived shortfalls / reneges / bumping right after t=5 while
build-to-stock scrambles to refill, then a new equilibrium. That is exactly the
kind of dynamic this model exists to study.
"""

import os
import sys

# Make the model/ package importable. When the GUI loads this module the runner
# has already put model/ on sys.path, so this is a harmless no-op; it only matters
# for running this file standalone (python reference_demo.py).
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "model")))

from agent import agent
from schedules import fixed, linear, sinusoidal, sequence
from simulation import Simulation
from tier import Tier


# A hand-written per-tick demand path: flat at 20, then a sharp jump to 60 at t=5.
# `sequence` indexes this list by t and holds the final value for any t past the
# end (forecasts query beyond simulation_length), so the list need only cover the
# run plus a little headroom.
DEMAND_RAMP = [20, 20, 20, 20, 20, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60]


def build():
    """Return an un-run Simulation wiring up the whole chain. Called by the GUI."""

    # --- tier 1: raw producers ------------------------------------------------
    # Top of chain: no upstream tier, raw materials assumed free. `cost` is the
    # production cost basis the bid optimiser uses. supply_fn is START capacity
    # (units begun per tick), NOT deliverable supply -- deliverable supply comes
    # from completions tau ticks later. The two producers differ only in
    # penalty_scale to make bumping observable: raw_sticky perceives a bump
    # penalty as 2x its contractual value, so it clings to existing customers;
    # raw_fickle (scale 1.0) will abandon a customer the moment a better offer
    # appears. These are durable components -- no shelf_life, so they never spoil.
    raw = Tier("raw", role="producer", agents=[
        agent(name="raw_sticky", cost=50,
              supply_fn=linear(start=40, slope=0, floor=0),
              production_time=2, inventory=40, safety_stock=40,
              penalty_scale=2.0),
        agent(name="raw_fickle", cost=50,
              supply_fn=linear(start=40, slope=0, floor=0),
              production_time=2, inventory=40, safety_stock=40,
              penalty_scale=1.0),
    ])

    # --- tier 2: assemblers (intermediaries) ----------------------------------
    # Consume raw inputs (input_inventory) and value-add into finished goods.
    #   cost=70            all-in optimiser basis (input + value-add estimate)
    #   production_cost=15 the value-add alone, charged at production start; the
    #                      input cost is charged separately when inputs are bought,
    #                      so the balance update isn't double-counted.
    #   revenue_forecast   the resale price the assembler expects downstream; this
    #                      drives how aggressively it bids for inputs.
    #   shelf_life=4       PERISHABILITY on an assembler hits BOTH pools -- raw
    #                      inputs AND finished goods spoil 4 ticks after entering.
    #                      Inputs are consumed FIFO (oldest first) so they're used
    #                      before they spoil; whatever isn't is written off.
    asm = Tier("asm", role="intermediary", agents=[
        agent(name="asm_1", cost=70, production_cost=15,
              supply_fn=linear(start=40, slope=0, floor=0),
              production_time=2, inventory=40, input_inventory=40,
              safety_stock=40, revenue_forecast_fn=fixed(110),
              penalty_scale=1.2, shelf_life=4),
        agent(name="asm_2", cost=70, production_cost=15,
              supply_fn=linear(start=40, slope=0, floor=0),
              production_time=2, inventory=40, input_inventory=40,
              safety_stock=40, revenue_forecast_fn=fixed(110),
              penalty_scale=1.2, shelf_life=4),
    ])

    # --- tier 3: retailers ----------------------------------------------------
    # Bottom of chain: serve exogenous end-customer demand from finished stock;
    # any shortfall is simply lost demand (no penalty). production_time defaults
    # to 0 and there's no input_inventory, so deliveries land straight in stock.
    #   shop_A  demand is the hand-written DEMAND_RAMP (the SHOCK). revenue_fn is
    #           the per-unit price it earns from customers.
    #   shop_B  demand is a smooth sinusoidal instead -- showing a FORMULA-based
    #           schedule alongside shop_A's LIST-based one, both serving the same
    #           upstream chain so they compete for supply during the spike.
    #   shelf_life=3  finished goods perish after 3 ticks (retailers have no input
    #           pool, so only finished stock is at risk).
    shop = Tier("shop", role="retailer", agents=[
        agent(name="shop_A", demand_fn=sequence(DEMAND_RAMP),
              revenue_fn=fixed(150), revenue_forecast_fn=fixed(150),
              safety_stock=10, inventory=10, shelf_life=3),
        agent(name="shop_B", demand_fn=sinusoidal(base=25, magnitude=5, frequency=0.1),
              revenue_fn=linear(start=140, slope=1),
              revenue_forecast_fn=linear(start=140, slope=1),
              safety_stock=8, inventory=8, shelf_life=3),
    ])

    # Tiers are passed top-of-chain first. Simulation derives the upstream wiring,
    # the trading order, and the delivery order from this list automatically.
    #   forecast_window   how many ticks ahead each agent plans / trades
    #   simulation_length last tick index (the run covers t = 0 .. length)
    return Simulation(tiers=[raw, asm, shop],
                      forecast_window=8, simulation_length=15)


if __name__ == "__main__":
    # Standalone headless run for quick checks outside the GUI. The GUI never
    # reaches this -- it imports the module and calls build() directly.
    build().run()
