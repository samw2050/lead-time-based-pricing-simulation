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
