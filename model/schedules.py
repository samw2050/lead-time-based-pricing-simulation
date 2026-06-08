"""Function factories for supply / demand / revenue schedules.

Each returns a callable f(t) producing the per-tick value. Used wherever the
agent constructor takes a *_fn argument. New families slot in here.
"""

import math
import random


def fixed(quantity):
    return lambda _: quantity

def linear(start, slope=-1, floor=0):
    return lambda t: max(start + slope * t, floor)

def sinusoidal(base, magnitude=1.0, frequency=1.0, phase=0.0):
    # period = 1/frequency ticks; oscillates between base-magnitude and base+magnitude.
    # phase is in radians.
    return lambda t: base + magnitude * math.sin(2 * math.pi * frequency * t + phase)

def random_uniform(low, high):
    # Redrawn independently each call; t is ignored.
    return lambda _: random.uniform(low, high)

def sequence(values, hold_last=True):
    # List-indexed schedule: f(t) returns values[t], so you can hand-write an
    # explicit per-tick path like [5, 5, 5, 5, 50, 50, 50] rather than fitting a
    # formula. Past the end of the list, f holds the last value (hold_last=True,
    # the default) or returns 0 -- needed because forecasts query t beyond
    # simulation_length. t < 0 clamps to the first value.
    values = list(values)
    def f(t):
        if not values:
            return 0
        if t < 0:
            return values[0]
        if t < len(values):
            return values[t]
        return values[-1] if hold_last else 0
    return f
