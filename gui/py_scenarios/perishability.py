"""Example custom Python scenario for the GUI.

A Python scenario builds the Simulation in code, so it can use anything the model
exposes -- arbitrary schedule functions, custom wiring, agent subclasses -- rather
than only the fields the JSON scenario builder understands. The only contract the
GUI requires is a module-level ``build()`` that returns an UN-RUN Simulation: the
runner drives its ticks and serialises each snapshot exactly as it does for a JSON
scenario. Do not call ``.run()`` here -- the runner advances ticks itself.

This example: three independent agents whose starting inventory perishes after 1,
2 and 3 ticks, with no supply and no demand -- so the only force acting on stock is
spoilage. Watch each agent's inventory survive exactly its shelf_life, then get
written off in one go.
"""

import os
import sys

# Make model/ importable for standalone runs; a no-op when the GUI loads this
# (the runner already puts model/ on sys.path).
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "model")))

from agent import agent
from simulation import Simulation
from tier import Tier


def build():
    """Return an un-run Simulation. Called by the GUI's /api/load_module."""
    shelf = Tier("shelf", role="producer", agents=[
        agent(name="fresh_1", inventory=30, shelf_life=1),
        agent(name="fresh_2", inventory=30, shelf_life=2),
        agent(name="fresh_3", inventory=30, shelf_life=3),
    ])
    return Simulation(tiers=[shelf], forecast_window=2, simulation_length=4)


if __name__ == "__main__":
    # Standalone run (headless) for quick checks outside the GUI.
    build().run()
