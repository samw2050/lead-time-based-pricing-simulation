"""Flask app: serves the GUI and exposes the simulation control API.

Run with ``python gui/app.py`` then open http://localhost:5000.
"""

import importlib.util
import json
import os

from flask import Flask, Response, jsonify, render_template, request

from runner import runner

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCENARIO_DIR = os.path.join(APP_DIR, "scenarios")
# Python scenario modules: each is a .py file exposing a build() that returns an
# un-run Simulation. Unlike JSON scenarios these can use anything the model code
# exposes (arbitrary schedule functions, custom wiring) rather than only the
# fields the JSON builder understands. The model/ dir is already on sys.path
# (runner adds it at import), so a module's `from agent import agent` resolves.
PY_SCENARIO_DIR = os.path.join(APP_DIR, "py_scenarios")

app = Flask(__name__)


# --- page ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# --- scenarios -------------------------------------------------------------

def _scenario_path(name):
    # Guard against path traversal: keep to a bare stem + .json.
    safe = os.path.basename(name)
    if not safe.endswith(".json"):
        safe += ".json"
    return os.path.join(SCENARIO_DIR, safe)


@app.route("/api/scenarios")
def list_scenarios():
    os.makedirs(SCENARIO_DIR, exist_ok=True)
    names = sorted(f[:-5] for f in os.listdir(SCENARIO_DIR) if f.endswith(".json"))
    return jsonify(names)


@app.route("/api/scenario/<name>")
def get_scenario(name):
    path = _scenario_path(name)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/scenario", methods=["POST"])
def save_scenario():
    body = request.get_json(force=True)
    name = body.get("name")
    config = body.get("config")
    if not name or config is None:
        return jsonify({"error": "name and config required"}), 400
    os.makedirs(SCENARIO_DIR, exist_ok=True)
    with open(_scenario_path(name), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return jsonify({"ok": True, "name": os.path.basename(name)})


@app.route("/api/scenario/<name>", methods=["DELETE"])
def delete_scenario(name):
    path = _scenario_path(name)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    os.remove(path)
    return jsonify({"ok": True, "name": os.path.basename(name)})


# --- python scenario modules -----------------------------------------------

def _module_dirs():
    # Directories searched for Python scenario modules. PY_SCENARIO_DIR (the tracked
    # built-ins) always comes first, followed by any extra dirs listed in the
    # MODEL3_PY_SCENARIO_DIRS env var (os.pathsep-separated). This lets you surface
    # scenarios that live OUTSIDE the repo's tracked tree -- e.g. a gitignored
    # research/ folder -- in the GUI without copying them in. Only existing dirs are
    # returned; PY_SCENARIO_DIR is created on demand by the callers that need it.
    dirs = [PY_SCENARIO_DIR]
    extra = os.environ.get("MODEL3_PY_SCENARIO_DIRS", "")
    dirs += [os.path.abspath(p.strip()) for p in extra.split(os.pathsep) if p.strip()]
    seen, out = set(), []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _module_path(name):
    # Resolve a module stem to a file, searching the configured dirs in order.
    # os.path.basename guards against path traversal (a name can't escape its dir);
    # the first directory that has <name>.py wins, so built-ins shadow extras.
    safe = os.path.basename(name)
    if not safe.endswith(".py"):
        safe += ".py"
    for d in _module_dirs():
        candidate = os.path.join(d, safe)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(PY_SCENARIO_DIR, safe)  # fall back for the not-found error


@app.route("/api/modules")
def list_modules():
    os.makedirs(PY_SCENARIO_DIR, exist_ok=True)
    # Aggregate scenario stems across every configured dir, de-duplicated (a stem
    # found in an earlier dir shadows a same-named one later, matching _module_path).
    seen, names = set(), []
    for d in _module_dirs():
        for f in os.listdir(d):
            stem = f[:-3]
            if f.endswith(".py") and not f.startswith("_") and stem not in seen:
                seen.add(stem)
                names.append(stem)
    return jsonify(sorted(names))


@app.route("/api/load_module", methods=["POST"])
def load_module():
    body = request.get_json(force=True)
    name = body.get("module")
    if not name:
        return jsonify({"error": "module name required"}), 400
    path = _module_path(name)
    if not os.path.exists(path):
        return jsonify({"error": "module not found"}), 404
    try:
        spec = importlib.util.spec_from_file_location(
            f"py_scenario_{os.path.basename(path)[:-3]}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "build"):
            raise AttributeError(
                "module must define build() returning an un-run Simulation")
        sim = mod.build()
        snap = runner.load_sim(sim)
    except Exception as exc:  # surface import / build errors to the UI
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400
    return jsonify(snap)


# --- simulation control ----------------------------------------------------

@app.route("/api/load", methods=["POST"])
def load():
    body = request.get_json(force=True)
    # Accept either an inline scenario config or a saved scenario name.
    if "name" in body and "config" not in body:
        path = _scenario_path(body["name"])
        if not os.path.exists(path):
            return jsonify({"error": "scenario not found"}), 404
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = body.get("config", body)
    try:
        snap = runner.load(config)
    except Exception as exc:  # surface build errors to the UI
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400
    return jsonify(snap)


@app.route("/api/play", methods=["POST"])
def play():
    runner.play()
    return jsonify({"playing": runner.playing})


@app.route("/api/stop", methods=["POST"])
def stop():
    runner.stop()
    return jsonify({"playing": runner.playing})


@app.route("/api/step", methods=["POST"])
def step():
    snap = runner.step()
    if snap is None:
        return jsonify({"error": "no simulation loaded or run finished"}), 400
    return jsonify(snap)


@app.route("/api/speed", methods=["POST"])
def speed():
    body = request.get_json(force=True)
    runner.set_speed(body.get("speed", 2.0))
    return jsonify({"speed": runner.speed})


@app.route("/api/stream")
def stream():
    def gen():
        for snap in runner.snapshots():
            if snap is None:
                # keep-alive comment so proxies don't close the connection
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(snap)}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    print("Supply-chain GUI running at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False)
