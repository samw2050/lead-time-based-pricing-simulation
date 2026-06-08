"""SimulationRunner: owns the live Simulation and drives it one tick at a time.

The model is written with flat imports (``from agent import agent``), so the
``model/`` directory must be on sys.path before anything here imports it. We do
that at import time below.

A background thread advances ticks while ``playing`` is set, pushing each
snapshot onto a queue that the SSE route drains. ``step()`` advances exactly one
tick and is also used directly by the /api/step endpoint.
"""

import contextlib
import io
import os
import queue
import sys
import threading
import time

_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model")
if _MODEL_DIR not in sys.path:
    sys.path.insert(0, _MODEL_DIR)

from solver import solve  # noqa: E402  (model module, needs sys.path above)

import scenario as scenario_mod  # noqa: E402
import serialize  # noqa: E402


class SimulationRunner:
    def __init__(self):
        self.sim = None
        self.config = None
        self.playing = False
        self.speed = 2.0  # ticks per second while playing
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._thread = None

    # --- lifecycle ---------------------------------------------------------

    def load(self, config):
        """Build a fresh Simulation from a scenario dict and reset play state."""
        with self._lock:
            self.stop()
            self.config = config
            self.sim = scenario_mod.build_simulation(config)
            # Drain any stale snapshots from a previous run.
            self._drain_queue()
            return serialize.snapshot(self.sim, log_text="")

    def load_sim(self, sim):
        """Load an already-constructed Simulation and reset play state.

        Used for custom Python scenario modules that build the Simulation in code
        (via their build()), bypassing the JSON scenario builder. Everything
        downstream -- ticking, snapshotting, streaming -- is config-agnostic, so a
        hand-built Simulation drives the GUI identically to a JSON-built one.
        config is set to None since there's no scenario dict behind this run."""
        with self._lock:
            self.stop()
            self.config = None
            self.sim = sim
            self._drain_queue()
            return serialize.snapshot(self.sim, log_text="")

    @property
    def loaded(self):
        return self.sim is not None

    @property
    def done(self):
        return self.sim is not None and self.sim.t > self.sim.simulation_length

    # --- stepping ----------------------------------------------------------

    def step(self):
        """Advance exactly one tick, capturing its printed output. Mirrors the
        body of Simulation.run()'s loop. Returns a snapshot, or None if there is
        no simulation or it has finished."""
        with self._lock:
            if self.sim is None or self.done:
                return None
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.sim.tick()
                self.sim.t += 1
                solve.cache_clear()
            return serialize.snapshot(self.sim, log_text=buf.getvalue())

    # --- play thread -------------------------------------------------------

    def play(self):
        if self.sim is None or self.done:
            return
        if self.playing:
            return
        self.playing = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.playing = False

    def set_speed(self, speed):
        self.speed = max(0.25, min(float(speed), 60.0))

    def _run_loop(self):
        while self.playing:
            snap = self.step()
            if snap is None:
                break
            self._queue.put(snap)
            if snap["done"]:
                self.playing = False
                break
            time.sleep(1.0 / self.speed)
        self.playing = False

    # --- SSE plumbing ------------------------------------------------------

    def snapshots(self, timeout=0.5):
        """Generator yielding snapshots as the play thread produces them. Yields
        None on idle ticks so the SSE route can emit keep-alives / check state."""
        while True:
            try:
                yield self._queue.get(timeout=timeout)
            except queue.Empty:
                yield None

    def _drain_queue(self):
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass


# A single process-wide runner instance the Flask app shares.
runner = SimulationRunner()
