/* Front-end controller: wires controls, the SSE stream, and the scenario
   builder to the backend, and fans each snapshot out to the map + graph. */

(() => {
  const $ = (id) => document.getElementById(id);
  let eventSource = null;
  let loaded = false;

  // --- snapshot fan-out ----------------------------------------------------

  function applySnapshot(snap, { resetGraph = false } = {}) {
    $("tick-label").textContent = "t = " + snap.t;
    MapView.setEdges(snap.edges || []);
    MapView.render(snap.tiers, snap.events || []);
    if (resetGraph) GraphView.reset();
    else GraphView.push(snap.t, snap.events || []);
    if (snap.log) appendReadout(snap.log);
    if (snap.done) {
      stopStream();
      setStatus("Run finished at t = " + snap.t);
      setPlaying(false);
    }
  }

  function appendReadout(text) {
    const el = $("readout");
    el.textContent += text;
    el.scrollTop = el.scrollHeight;
  }

  function setStatus(msg) { $("status-msg").textContent = msg || ""; }

  function setPlaying(playing) {
    $("btn-play").disabled = playing || !loaded;
    $("btn-stop").disabled = !playing;
    $("btn-step").disabled = playing || !loaded;
  }

  // --- SSE -----------------------------------------------------------------

  function startStream() {
    if (eventSource) return;
    eventSource = new EventSource("/api/stream");
    eventSource.onmessage = (e) => applySnapshot(JSON.parse(e.data));
    eventSource.onerror = () => { /* browser auto-reconnects */ };
  }
  function stopStream() {
    if (eventSource) { eventSource.close(); eventSource = null; }
  }

  // --- controls ------------------------------------------------------------

  async function doLoad() {
    const name = $("scenario-select").value;
    if (!name) return;
    setStatus("Loading " + name + "…");
    const res = await fetch("/api/load", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const snap = await res.json();
    if (!res.ok) { setStatus("Error: " + snap.error); return; }
    loaded = true;
    $("readout").textContent = "";
    applySnapshot(snap, { resetGraph: true });
    setPlaying(false);
    setStatus("Loaded " + name);
  }

  async function doPlay() {
    if (!loaded) return;
    startStream();
    await fetch("/api/play", { method: "POST" });
    setPlaying(true);
    setStatus("Playing…");
  }

  async function doStop() {
    await fetch("/api/stop", { method: "POST" });
    stopStream();
    setPlaying(false);
    setStatus("Stopped");
  }

  async function doStep() {
    const res = await fetch("/api/step", { method: "POST" });
    const snap = await res.json();
    if (!res.ok) { setStatus(snap.error); return; }
    applySnapshot(snap);
  }

  async function setSpeed(v) {
    $("speed-val").textContent = v;
    await fetch("/api/speed", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed: parseFloat(v) }),
    });
  }

  async function refreshScenarioList(selectName) {
    const res = await fetch("/api/scenarios");
    const names = await res.json();
    const sel = $("scenario-select");
    sel.innerHTML = "";
    names.forEach(n => {
      const opt = document.createElement("option");
      opt.value = n; opt.textContent = n;
      sel.appendChild(opt);
    });
    if (selectName && names.includes(selectName)) sel.value = selectName;
  }

  // --- scenario builder ----------------------------------------------------

  const ROLES = ["producer", "intermediary", "retailer"];
  // Core agent fields exposed in the builder. type: number | schedule.
  const AGENT_FIELDS = [
    { key: "cost", type: "number" },
    { key: "production_cost", type: "number" },
    { key: "production_time", type: "number" },
    { key: "inventory", type: "number" },
    { key: "input_inventory", type: "number" },
    { key: "safety_stock", type: "number" },
    { key: "penalty_scale", type: "number" },
    { key: "supply", type: "schedule" },
    { key: "demand", type: "schedule" },
    { key: "revenue", type: "schedule" },
    { key: "revenue_forecast", type: "schedule" },
  ];

  function makeAgentRow(prefill = {}) {
    const wrap = document.createElement("div");
    wrap.className = "sb-agent";
    const nameL = document.createElement("label");
    nameL.innerHTML = "name ";
    const nameI = document.createElement("input");
    nameI.type = "text"; nameI.placeholder = "(auto)";
    nameI.value = prefill.name || "";
    nameI.dataset.k = "name"; nameI.style.width = "90px";
    nameL.appendChild(nameI); wrap.appendChild(nameL);

    AGENT_FIELDS.forEach(f => {
      const l = document.createElement("label");
      l.textContent = f.key + " ";
      const inp = document.createElement("input");
      inp.type = "text";
      inp.dataset.k = f.key; inp.dataset.kind = f.type;
      inp.placeholder = f.type === "schedule" ? "type:args" : "";
      if (prefill[f.key] !== undefined) inp.value = stringifyField(prefill[f.key]);
      l.appendChild(inp); wrap.appendChild(l);
    });
    const del = document.createElement("button");
    del.className = "small"; del.textContent = "×";
    del.onclick = () => wrap.remove();
    wrap.appendChild(del);
    return wrap;
  }

  function stringifyField(v) {
    if (v && typeof v === "object") {
      // schedule dict -> "type:a,b,c" using its declared params order is hard;
      // show as compact "type:k=v;..." which parseSchedule understands.
      const { type, ...rest } = v;
      const parts = Object.entries(rest).map(([k, val]) => `${k}=${val}`);
      return type + (parts.length ? ":" + parts.join(",") : "");
    }
    return String(v);
  }

  // Parse a schedule cell. Accepts "fixed:quantity=100" or shorthand
  // "linear:130,1" (positional start,slope) etc.
  function parseSchedule(str) {
    str = str.trim();
    if (!str) return undefined;
    const [type, argStr] = str.split(":");
    const spec = { type: type.trim() };
    if (argStr) {
      argStr.split(",").forEach((part, i) => {
        part = part.trim();
        if (part.includes("=")) {
          const [k, v] = part.split("=");
          spec[k.trim()] = numOr(v.trim());
        } else {
          // positional: map by common factory signatures
          const pos = POSITIONAL[type.trim()] || [];
          if (pos[i]) spec[pos[i]] = numOr(part);
        }
      });
    }
    return spec;
  }
  const POSITIONAL = {
    fixed: ["quantity"],
    linear: ["start", "slope", "floor"],
    sinusoidal: ["base", "magnitude", "frequency", "phase"],
    random_uniform: ["low", "high"],
  };
  function numOr(s) { const n = parseFloat(s); return isNaN(n) ? s : n; }

  function makeTierBlock(prefill = {}) {
    const block = document.createElement("div");
    block.className = "sb-tier";

    const head = document.createElement("div");
    head.className = "sb-tier-head";
    head.innerHTML = `
      <label>tier name <input type="text" class="sb-tier-name" value="${prefill.name || ""}"></label>
      <label>role <select class="sb-tier-role">${ROLES.map(r => `<option ${r === prefill.role ? "selected" : ""}>${r}</option>`).join("")}</select></label>
      <label class="sb-mode">mode
        <select class="sb-tier-mode">
          <option value="list">explicit agents</option>
          <option value="count">count + defaults</option>
        </select>
      </label>
      <span class="sb-count-wrap" style="display:none">
        <label>count <input type="number" class="sb-tier-count" value="2" min="1" style="width:60px"></label>
      </span>
    `;
    block.appendChild(head);

    const agentsWrap = document.createElement("div");
    agentsWrap.className = "sb-agents";
    block.appendChild(agentsWrap);

    const rowActions = document.createElement("div");
    rowActions.className = "sb-row-actions";
    const addAgent = document.createElement("button");
    addAgent.className = "small"; addAgent.textContent = "+ agent / default";
    addAgent.onclick = () => agentsWrap.appendChild(makeAgentRow());
    const delTier = document.createElement("button");
    delTier.className = "small"; delTier.textContent = "× tier";
    delTier.onclick = () => block.remove();
    rowActions.append(addAgent, delTier);
    block.appendChild(rowActions);

    // mode toggle shows/hides the count field
    head.querySelector(".sb-tier-mode").onchange = (e) => {
      head.querySelector(".sb-count-wrap").style.display =
        e.target.value === "count" ? "inline" : "none";
    };

    // seed with one agent row (or provided ones)
    if (prefill.agents && prefill.agents.length) {
      prefill.agents.forEach(a => agentsWrap.appendChild(makeAgentRow(a)));
    } else {
      agentsWrap.appendChild(makeAgentRow());
    }
    return block;
  }

  function collectAgent(row) {
    const out = {};
    row.querySelectorAll("input").forEach(inp => {
      const v = inp.value.trim();
      if (!v) return;
      if (inp.dataset.kind === "schedule") out[inp.dataset.k] = parseSchedule(v);
      else if (inp.dataset.k === "name") out.name = v;
      else out[inp.dataset.k] = numOr(v);
    });
    return out;
  }

  function collectScenario() {
    const tiers = [];
    document.querySelectorAll(".sb-tier").forEach(block => {
      const name = block.querySelector(".sb-tier-name").value.trim();
      const role = block.querySelector(".sb-tier-role").value;
      const mode = block.querySelector(".sb-tier-mode").value;
      const rows = [...block.querySelectorAll(".sb-agent")].map(collectAgent);
      if (mode === "count") {
        const count = parseInt(block.querySelector(".sb-tier-count").value, 10) || 1;
        tiers.push({ name, role, count, defaults: rows[0] || {} });
      } else {
        tiers.push({ name, role, agents: rows });
      }
    });
    return {
      forecast_window: parseInt($("sb-fw").value, 10) || 12,
      simulation_length: parseInt($("sb-len").value, 10) || 120,
      tiers,
    };
  }

  async function saveScenario() {
    const name = $("sb-name").value.trim();
    if (!name) { $("sb-msg").textContent = "Name required"; return; }
    const config = collectScenario();
    const res = await fetch("/api/scenario", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config }),
    });
    const data = await res.json();
    if (!res.ok) { $("sb-msg").textContent = "Error: " + data.error; return; }
    $("sb-msg").textContent = "Saved " + data.name;
    await refreshScenarioList(name);
  }

  // --- builder modal -------------------------------------------------------

  function openBuilder() { $("builder-modal").classList.remove("hidden"); }
  function closeBuilder() { $("builder-modal").classList.add("hidden"); }
  function isBuilderOpen() { return !$("builder-modal").classList.contains("hidden"); }

  // --- init ----------------------------------------------------------------

  function bind() {
    $("btn-play").onclick = doPlay;
    $("btn-stop").onclick = doStop;
    $("btn-step").onclick = doStep;
    $("btn-load").onclick = doLoad;
    $("speed").oninput = (e) => setSpeed(e.target.value);
    $("metric-select").onchange = (e) => MapView.setMetric(e.target.value);
    $("toggle-readout").onclick = () => {
      $("readout-panel").classList.toggle("collapsed");
    };
    $("sb-add-tier").onclick = () => $("sb-tiers").appendChild(makeTierBlock());
    $("sb-save").onclick = saveScenario;

    // builder modal: open via button, close via ×, backdrop click, or Escape
    $("btn-open-builder").onclick = openBuilder;
    $("btn-close-builder").onclick = closeBuilder;
    $("builder-modal").onclick = (e) => {
      if (e.target === $("builder-modal")) closeBuilder();
    };
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isBuilderOpen()) closeBuilder();
    });
  }

  window.addEventListener("DOMContentLoaded", async () => {
    MapView.init();
    GraphView.init();
    bind();
    await refreshScenarioList("default");
    // Seed the builder with one example tier.
    $("sb-tiers").appendChild(makeTierBlock({ name: "tier1", role: "producer" }));
  });
})();
