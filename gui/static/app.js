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
    // Raw-supply tokens represent a tick advancing; skip on the initial load
    // snapshot (resetGraph), where the sim hasn't stepped yet.
    if (!resetGraph) MapView.flowRaw();
    if (resetGraph) GraphView.reset();
    else GraphView.push(snap.t, snap.events || []);
    BumpView.update(snap.bump_curves || []);
    StakeView.update(snap.bump_curves || [], snap.stake_axis);
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
    const sel = $("scenario-select");
    const name = sel.value;
    if (!name) return;
    // The selected <option> carries data-kind ("json" or "module"), set when the
    // picker is populated, so a JSON scenario and a Python module that happen to
    // share a name still dispatch to the right endpoint.
    const opt = sel.options[sel.selectedIndex];
    const isModule = opt && opt.dataset.kind === "module";
    setStatus("Loading " + name + "…");
    const res = isModule
      ? await fetch("/api/load_module", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ module: name }),
        })
      : await fetch("/api/load", {
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
    const [jsonRes, modRes] = await Promise.all([
      fetch("/api/scenarios"),
      fetch("/api/modules"),
    ]);
    const names = await jsonRes.json();
    const modules = modRes.ok ? await modRes.json() : [];

    // Builder's "edit existing" picker: JSON scenarios only -- a Python module
    // isn't editable through the form builder.
    const sbSel = $("sb-existing");
    if (sbSel) {
      sbSel.innerHTML = "";
      names.forEach(n => {
        const opt = document.createElement("option");
        opt.value = n; opt.textContent = n;
        sbSel.appendChild(opt);
      });
      if (selectName && names.includes(selectName)) sbSel.value = selectName;
    }

    // Run selector: JSON scenarios + Python modules, in labelled groups. Each
    // option tags its kind so doLoad() can route to the right endpoint.
    const runSel = $("scenario-select");
    if (runSel) {
      runSel.innerHTML = "";
      const addGroup = (label, items, kind) => {
        if (!items.length) return;
        const g = document.createElement("optgroup");
        g.label = label;
        items.forEach(n => {
          const opt = document.createElement("option");
          opt.value = n; opt.textContent = n; opt.dataset.kind = kind;
          g.appendChild(opt);
        });
        runSel.appendChild(g);
      };
      addGroup("Scenarios (JSON)", names, "json");
      addGroup("Python modules", modules, "module");
      if (selectName) {
        const match = Array.from(runSel.options).find(o => o.value === selectName);
        if (match) runSel.value = selectName;
      }
    }
  }

  // --- scenario builder ----------------------------------------------------

  const ROLES = ["producer", "intermediary", "retailer"];
  // Scalar agent fields shown as plain number boxes.
  const SCALAR_FIELDS = [
    "cost", "production_cost", "production_time", "inventory",
    "input_inventory", "safety_stock", "shelf_life", "penalty_scale",
  ];
  // Schedule-valued agent fields, shown as a type dropdown + parameter boxes.
  const SCHEDULE_FIELDS = ["supply", "demand", "revenue", "revenue_forecast"];

  // Each schedule family and its parameters with default values. Mirrors the
  // factories in model/schedules.py so the picker shows exactly what's buildable.
  const SCHEDULE_TYPES = {
    fixed: { quantity: 100 },
    linear: { start: 50, slope: -1, floor: 0 },
    sinusoidal: { base: 50, magnitude: 1, frequency: 1, phase: 0 },
    random_uniform: { low: 0, high: 100 },
  };

  // Per-role agent defaults so a freshly-built chain actually trades out of the
  // box (matches gui/scenarios/default.json). A field set to null / "" is left
  // unset, falling back to the agent constructor default. These values are
  // written straight into the builder's boxes so the user sees what they'll get.
  const ROLE_DEFAULTS = {
    producer: {
      cost: 50, production_cost: "", production_time: 2,
      inventory: 50, input_inventory: "", safety_stock: 50, penalty_scale: 1,
      supply: { type: "linear", start: 50, slope: 0, floor: 0 },
    },
    intermediary: {
      cost: 60, production_cost: 12, production_time: 2,
      inventory: 50, input_inventory: 50, safety_stock: 50, penalty_scale: 1.1,
      supply: { type: "linear", start: 50, slope: 0, floor: 0 },
      revenue_forecast: { type: "fixed", quantity: 100 },
    },
    retailer: {
      inventory: 5, safety_stock: 5,
      demand: { type: "sinusoidal", base: 50, magnitude: 10, frequency: 6, phase: 0 },
      revenue: { type: "linear", start: 130, slope: 1 },
      revenue_forecast: { type: "linear", start: 130, slope: 1 },
    },
  };

  // "production_cost" -> "Production cost" for display labels.
  const prettyLabel = (k) => {
    const s = k.replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  };

  function numOr(s) { const n = parseFloat(s); return isNaN(n) ? s : n; }
  const hasVal = (v) => v !== undefined && v !== null && v !== "";

  // Render the parameter boxes for one schedule type into `container`. Pre-fills
  // from `value` (an existing schedule dict) where given, else the type default.
  function renderSchedParams(container, type, value = {}) {
    container.innerHTML = "";
    const params = SCHEDULE_TYPES[type];
    if (!params) return;
    Object.entries(params).forEach(([p, def]) => {
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "any";
      inp.className = "sb-sched-param"; inp.dataset.p = p;
      inp.title = p; inp.placeholder = p;
      inp.value = value[p] !== undefined ? value[p] : def;
      container.appendChild(inp);
    });
  }

  // A schedule field: label + type dropdown (incl. "(none)") + its param boxes.
  function makeScheduleField(key, value) {
    const wrap = document.createElement("label");
    wrap.className = "sb-sched";
    wrap.dataset.k = key; wrap.dataset.kind = "schedule";

    const title = document.createElement("span");
    title.className = "sb-sched-title";
    title.textContent = prettyLabel(key) + " ";

    const sel = document.createElement("select");
    sel.className = "sb-sched-type";
    sel.appendChild(new Option("(none)", ""));
    Object.keys(SCHEDULE_TYPES).forEach(t => sel.appendChild(new Option(prettyLabel(t), t)));

    const params = document.createElement("span");
    params.className = "sb-sched-params";
    sel.onchange = () => renderSchedParams(params, sel.value);

    if (value && value.type) {
      sel.value = value.type;
      renderSchedParams(params, value.type, value);
    }
    wrap.append(title, sel, params);
    return wrap;
  }

  function makeAgentRow(prefill = {}) {
    const wrap = document.createElement("div");
    wrap.className = "sb-agent";

    const nameL = document.createElement("label");
    nameL.textContent = "Name ";
    const nameI = document.createElement("input");
    nameI.type = "text"; nameI.placeholder = "(auto)";
    nameI.value = prefill.name || "";
    nameI.dataset.k = "name"; nameI.dataset.kind = "name";
    nameI.style.width = "90px";
    nameL.appendChild(nameI); wrap.appendChild(nameL);

    SCALAR_FIELDS.forEach(key => {
      const l = document.createElement("label");
      l.textContent = prettyLabel(key) + " ";
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "any";
      inp.dataset.k = key; inp.dataset.kind = "number";
      // shelf_life is optional: a blank box means the agent's stock never spoils.
      // Hint that in the placeholder so the empty default reads as intentional.
      if (key === "shelf_life") { inp.placeholder = "∞ never spoils"; inp.min = 0; }
      if (hasVal(prefill[key])) inp.value = prefill[key];
      l.appendChild(inp); wrap.appendChild(l);
    });

    SCHEDULE_FIELDS.forEach(key => {
      wrap.appendChild(makeScheduleField(key, prefill[key]));
    });

    const del = document.createElement("button");
    del.type = "button"; del.className = "small"; del.textContent = "×";
    del.onclick = () => wrap.remove();
    wrap.appendChild(del);
    return wrap;
  }

  function makeTierBlock(prefill = {}) {
    const block = document.createElement("div");
    block.className = "sb-tier";
    const role = prefill.role || "producer";

    const head = document.createElement("div");
    head.className = "sb-tier-head";
    head.innerHTML = `
      <label>Tier name <input type="text" class="sb-tier-name" value="${prefill.name || ""}"></label>
      <label>Role <select class="sb-tier-role">${ROLES.map(r => `<option value="${r}" ${r === role ? "selected" : ""}>${prettyLabel(r)}</option>`).join("")}</select></label>
      <label class="sb-mode">Mode
        <select class="sb-tier-mode">
          <option value="list">Explicit agents</option>
          <option value="count">Count + defaults</option>
        </select>
      </label>
      <span class="sb-count-wrap" style="display:none">
        <label>Count <input type="number" class="sb-tier-count" value="2" min="1" style="width:60px"></label>
      </span>
    `;
    block.appendChild(head);

    const agentsWrap = document.createElement("div");
    agentsWrap.className = "sb-agents";
    block.appendChild(agentsWrap);

    const roleSel = head.querySelector(".sb-tier-role");
    const modeSel = head.querySelector(".sb-tier-mode");

    const rowActions = document.createElement("div");
    rowActions.className = "sb-row-actions";
    const addAgent = document.createElement("button");
    addAgent.type = "button"; addAgent.className = "small";
    addAgent.textContent = "+ Agent / default";
    addAgent.onclick = () =>
      agentsWrap.appendChild(makeAgentRow(ROLE_DEFAULTS[roleSel.value] || {}));
    const delTier = document.createElement("button");
    delTier.type = "button"; delTier.className = "small"; delTier.textContent = "× Tier";
    delTier.onclick = () => block.remove();
    rowActions.append(addAgent, delTier);
    block.appendChild(rowActions);

    // mode toggle shows/hides the count field
    modeSel.onchange = (e) => {
      head.querySelector(".sb-count-wrap").style.display =
        e.target.value === "count" ? "inline" : "none";
    };
    // Changing role re-seeds the rows with that role's defaults so the boxes
    // always reflect a working agent for the chosen role.
    roleSel.onchange = () => {
      agentsWrap.innerHTML = "";
      agentsWrap.appendChild(makeAgentRow(ROLE_DEFAULTS[roleSel.value] || {}));
    };

    // Seed rows: explicit agents, then count+defaults, else one role default.
    if (prefill.agents && prefill.agents.length) {
      prefill.agents.forEach(a => agentsWrap.appendChild(makeAgentRow(a)));
    } else if (prefill.defaults) {
      modeSel.value = "count";
      head.querySelector(".sb-count-wrap").style.display = "inline";
      head.querySelector(".sb-tier-count").value = prefill.count || 2;
      agentsWrap.appendChild(makeAgentRow(prefill.defaults));
    } else {
      agentsWrap.appendChild(makeAgentRow(ROLE_DEFAULTS[role] || {}));
    }
    return block;
  }

  function collectSchedule(wrap) {
    const type = wrap.querySelector(".sb-sched-type").value;
    if (!type) return undefined;
    const spec = { type };
    wrap.querySelectorAll(".sb-sched-param").forEach(inp => {
      const v = inp.value.trim();
      if (v !== "") spec[inp.dataset.p] = numOr(v);
    });
    return spec;
  }

  function collectAgent(row) {
    const out = {};
    const nameI = row.querySelector('input[data-kind="name"]');
    if (nameI && nameI.value.trim()) out.name = nameI.value.trim();
    row.querySelectorAll('input[data-kind="number"]').forEach(inp => {
      const v = inp.value.trim();
      if (v !== "") out[inp.dataset.k] = numOr(v);
    });
    row.querySelectorAll(".sb-sched").forEach(wrap => {
      const spec = collectSchedule(wrap);
      if (spec) out[wrap.dataset.k] = spec;
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
      obs_window: parseInt($("sb-obs").value, 10) || 300,
      tiers,
    };
  }

  // Populate the whole builder form from a saved scenario config.
  function loadIntoBuilder(name, config) {
    $("sb-name").value = name;
    $("sb-fw").value = config.forecast_window ?? 12;
    $("sb-len").value = config.simulation_length ?? 120;
    $("sb-obs").value = config.obs_window ?? 300;
    $("sb-tiers").innerHTML = "";
    (config.tiers || []).forEach(t => $("sb-tiers").appendChild(makeTierBlock(t)));
    $("sb-msg").textContent = "Editing " + name;
  }

  async function loadExistingIntoBuilder() {
    const name = $("sb-existing").value;
    if (!name) return;
    const res = await fetch("/api/scenario/" + encodeURIComponent(name));
    const config = await res.json();
    if (!res.ok) { $("sb-msg").textContent = "Error: " + config.error; return; }
    loadIntoBuilder(name, config);
  }

  // Write the current form to the scenario named `name`, overwriting if present.
  async function saveAs(name) {
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

  function saveScenario() { saveAs($("sb-name").value.trim()); }

  async function saveScenarioAsNew() {
    const suggested = ($("sb-name").value.trim() || "scenario") + "_copy";
    const name = (window.prompt("Save as new scenario named:", suggested) || "").trim();
    if (!name) return;
    $("sb-name").value = name;
    await saveAs(name);
  }

  async function deleteScenario() {
    const name = $("sb-name").value.trim();
    if (!name) { $("sb-msg").textContent = "Name required"; return; }
    if (!window.confirm(`Delete scenario "${name}"? This cannot be undone.`)) return;
    const res = await fetch("/api/scenario/" + encodeURIComponent(name), { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) { $("sb-msg").textContent = "Error: " + data.error; return; }
    $("sb-msg").textContent = "Deleted " + data.name;
    await refreshScenarioList();
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
    // The panel animates its flex-basis (.2s); re-measure the map once it settles
    // so the map fills the reclaimed width instead of staying bunched up.
    $("readout-panel").addEventListener("transitionend", (e) => {
      if (e.propertyName === "flex-basis") MapView.refresh();
    });
    $("sb-add-tier").onclick = () => $("sb-tiers").appendChild(makeTierBlock());
    $("sb-load-existing").onclick = loadExistingIntoBuilder;
    $("sb-save").onclick = saveScenario;
    $("sb-save-new").onclick = saveScenarioAsNew;
    $("sb-delete").onclick = deleteScenario;

    // Collapsible graph sections: clicking the header hides/shows its chart.
    document.querySelectorAll(".graphs h2").forEach(h => {
      h.onclick = () => h.parentElement.classList.toggle("collapsed");
    });

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
    BumpView.init();
    StakeView.init();
    bind();
    await refreshScenarioList("default");
    // Seed the builder with a working producer -> intermediary -> retailer chain
    // (role defaults trade out of the box) so a fresh scenario isn't inert.
    $("sb-tiers").appendChild(makeTierBlock({ name: "tier2", role: "producer" }));
    $("sb-tiers").appendChild(makeTierBlock({ name: "tier1", role: "intermediary" }));
    $("sb-tiers").appendChild(makeTierBlock({ name: "OEM", role: "retailer" }));
  });
})();
