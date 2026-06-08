"""Turn a JSON scenario description into a runnable Simulation.

A scenario is a plain dict (loaded from a saved JSON file or POSTed by the
scenario builder) of the shape::

    {
      "forecast_window": 12,
      "simulation_length": 120,
      "tiers": [
        {
          "name": "tier2",
          "role": "producer",
          "agents": [
            {"name": "tier2_1", "cost": 50,
             "supply": {"type": "linear", "start": 50, "slope": 0, "floor": 0},
             "production_time": 2, "inventory": 50, "safety_stock": 50,
             "penalty_scale": 1}
          ]
        },
        ...
      ]
    }

Instead of an explicit ``agents`` list a tier may give a count + defaults
shortcut::

    {"name": "tier2", "role": "producer", "count": 3, "defaults": {...}}

which expands to ``tier2_1 .. tier2_3`` all sharing the same params.

Schedule-valued fields (``supply``, ``demand``, ``revenue``,
``revenue_forecast``) are themselves small dicts ``{"type": ..., ...params}``
mapped to the factories in ``schedules.py``.
"""

from agent import agent
from schedules import fixed, linear, sinusoidal, random_uniform, sequence
from simulation import Simulation
from tier import Tier


# --- schedule dict -> callable ---------------------------------------------

_SCHEDULE_FACTORIES = {
    "fixed": fixed,
    "linear": linear,
    "sinusoidal": sinusoidal,
    "random_uniform": random_uniform,
    # sequence takes a list: {"type": "sequence", "values": [5, 5, 50, 50]}.
    # Not offered in the form builder (a list doesn't fit the numeric-param UI),
    # but usable from hand-edited JSON and from Python scenario modules.
    "sequence": sequence,
}


def build_schedule(spec):
    """Map a {"type": ..., ...params} dict to a schedule callable f(t)."""
    if spec is None:
        return None
    if callable(spec):
        return spec
    spec = dict(spec)
    kind = spec.pop("type", None)
    if kind not in _SCHEDULE_FACTORIES:
        raise ValueError(f"unknown schedule type {kind!r}; "
                         f"expected one of {sorted(_SCHEDULE_FACTORIES)}")
    return _SCHEDULE_FACTORIES[kind](**spec)


# --- agent dict -> agent ----------------------------------------------------

# Scalar params copied straight through to agent(...) when present.
_SCALAR_FIELDS = (
    "cost", "production_cost", "production_time", "inventory",
    "input_inventory", "safety_stock", "shelf_life", "penalty_scale",
    "risk_aversion",
)
# Fields whose value is a schedule spec, mapped name -> agent kwarg.
_SCHEDULE_FIELDS = {
    "supply": "supply_fn",
    "demand": "demand_fn",
    "revenue": "revenue_fn",
    "revenue_forecast": "revenue_forecast_fn",
}


def build_agent(spec, fallback_name, obs_window=300):
    """Construct one agent from its dict spec. Absent keys fall back to the
    agent constructor defaults, so a sparse spec is fine. ``obs_window`` is the
    scenario-level bump-model memory length, applied to every agent."""
    spec = dict(spec)
    kwargs = {"name": spec.get("name", fallback_name)}
    for field in _SCALAR_FIELDS:
        if field in spec and spec[field] is not None:
            kwargs[field] = spec[field]
    for field, kwarg in _SCHEDULE_FIELDS.items():
        if field in spec and spec[field] is not None:
            kwargs[kwarg] = build_schedule(spec[field])
    kwargs["obs_window"] = obs_window
    return agent(**kwargs)


def build_tier(spec, obs_window=300):
    """Construct one Tier, supporting both an explicit ``agents`` list and the
    ``count`` + ``defaults`` shortcut."""
    spec = dict(spec)
    name = spec["name"]
    role = spec.get("role", "intermediary")

    agent_specs = spec.get("agents")
    if agent_specs is None:
        count = int(spec.get("count", 0))
        defaults = spec.get("defaults", {})
        agent_specs = [dict(defaults) for _ in range(count)]

    agents = []
    for i, a_spec in enumerate(agent_specs, start=1):
        agents.append(build_agent(a_spec, fallback_name=f"{name}_{i}",
                                   obs_window=obs_window))
    return Tier(name, role=role, agents=agents)


def build_simulation(config):
    """Top-level entry: a scenario dict -> a constructed (un-run) Simulation."""
    # obs_window: bump-model memory length (number of recent observations kept per
    # supplier). null -> unbounded history. Applied to every agent in the scenario.
    obs_window = config.get("obs_window", 300)
    tiers = [build_tier(t, obs_window=obs_window) for t in config["tiers"]]
    return Simulation(
        tiers=tiers,
        forecast_window=int(config.get("forecast_window", 12)),
        simulation_length=int(config.get("simulation_length", 120)),
    )
