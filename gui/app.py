"""Flask app: serves the GUI and exposes the simulation control API.

Run with ``python gui/app.py`` then open http://localhost:5000.
"""

import json
import os

from flask import Flask, Response, jsonify, render_template, request

from runner import runner

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCENARIO_DIR = os.path.join(APP_DIR, "scenarios")

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
