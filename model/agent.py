"""The agent: a single firm in the supply chain.

Tracks finished and input inventory, runs its production lifecycle, forecasts
demand / supply / revenue over the planning window, learns per-supplier
bump-probability models, and prices the units it offers each auction.
"""

from collections import deque

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from schedules import fixed
from bump_model import DEFAULT_MODEL_SELF, DEFAULT_MODEL_OTHER


# Box constraints on the fitted logistic (w0, w_lead, w_stake) = (a, b, c).
# Only the stake coefficient is pinned: w_stake <= -MIN_STAKE_SENSITIVITY enforces
# the economically sane "- c * (price + penalty)" sign AND keeps c strictly away
# from 0. A floor matters because penalty is pure upside to a buyer (it collects the
# penalty on a bump), so its unconstrained optimum is
#   penalty* = (rev - price) + 1 / (c * (1 - p)),
# which diverges as c -> 0. Left at c >= 0 the L2-regularised fit drives w_stake to
# exactly 0 once stakes grow large (a vanishing coefficient still yields a large
# z = w_stake * stake when stake is huge, and L2 punishes |w_stake|), removing the
# only brake on penalty and letting it run away. Flooring c at the cold-start
# sensitivity (DEFAULT_MODEL_OTHER's C=0.001, calibrated to this model's stake
# magnitudes) caps penalty* at ~(rev-price)+a few thousand -- the same order as a
# normal contract -- so a fitted belief can never become less stake-sensitive than
# the baseline the model ships with. The intercept (a) and lead slope (b) are left
# free -- with the moving observation window letting stale beliefs decay, we want to
# watch how they settle before deciding whether to cap them too.
MIN_STAKE_SENSITIVITY = 0.001
BUMP_BOUNDS = [(None, None),                    # w0 = a       : free
               (None, None),                    # w_lead = b   : free
               (None, -MIN_STAKE_SENSITIVITY)]  # w_stake = -c : <= -0.001 (c >= 0.001)


class agent:
    def __init__(self, name=None, risk_aversion=0.001, demand_fn=None, supply_fn=None,
                 cost=0, production_cost=None,
                 revenue_fn=None, revenue_forecast_fn=None,
                 safety_stock=0, inventory=0,
                 production_time=0, input_inventory=None,
                 shelf_life=None,
                 build_to_stock_horizon=5,
                 ewma_alpha=0.3,
                 penalty_scale=1.0,
                 min_penalty=0.0, max_penalty=None,
                 min_obs_for_fit=5, l2_reg=0.1, obs_window=300):
        self.name = name
        self.risk_aversion = risk_aversion
        # How heavily this agent, ACTING AS A SELLER, weights a bump/renege penalty
        # in its own bump decision -- distinct from what the bumped customer actually
        # receives (always the unscaled contractual penalty). penalty_scale > 1 makes
        # the supplier more reluctant to abandon an existing customer: it perceives the
        # penalty it would pay as penalty_scale * the contractual amount, so it only
        # bumps when the new offer beats price + penalty*penalty_scale rather than
        # price + penalty. =1 is neutral (the customer's penalty and the supplier's
        # perceived cost coincide). Only producers/intermediaries act as sellers, so
        # this has no effect on retailers. See resale_cost / resale_floor in
        # Simulation._run_auction for where it is applied.
        self.penalty_scale = penalty_scale
        # The penalty window this agent, ACTING AS A SELLER, will accept on a new
        # contract. The (price, penalty) optimisation in the bid and negotiation
        # stages is constrained to penalty in [min_penalty, max_penalty], so the
        # counterparty must negotiate within it. min_penalty raises the floor (the
        # seller demands at least this much compensation for a renege); max_penalty
        # caps the seller's exposure (None = unbounded above, the original
        # behaviour). If no (price, penalty) inside the window satisfies both
        # parties' EV constraints, no contract forms. Only sellers
        # (producers/intermediaries) consult these, so they have no effect on
        # retailers. Applied in solver._solve_impl via the penalty bounds.
        self.min_penalty = min_penalty
        self.max_penalty = max_penalty
        # demand_fn=None -> intermediary: demand is derived from contracts sold downstream.
        # supply_fn=None -> non-producer: supply comes from inbound contracts + starting inventory.
        self.demand_fn = demand_fn
        self.supply_fn = supply_fn
        self.demand_forecast = {}
        # Logistic regression of P(bump) = sigmoid(w0 + w_lead*lead_frac + w_stake*(price+penalty)),
        # keyed by supplier (None = self). Buyer-side: one model per upstream supplier,
        # trained on delivered (label 0) vs reneged/bumped-out (label 1) contracts from
        # that supplier. Seller-side: a single self-model under key None, trained on
        # this agent's own deliveries vs own reneges/bump-outs.
        # `observations[supplier]` is a bounded deque of the most recent
        # (lead_frac, price+penalty, bump) triples -- maxlen=obs_window so stale
        # beliefs decay (old failures age out once fresh data arrives), which lets a
        # self-censored agent recover instead of staying pinned on early bad luck.
        # obs_window=None keeps the full unbounded history.
        # `bump_models[supplier]` holds the most recently fitted (w0, w_lead, w_stake)
        # tuple. Missing entries fall back to DEFAULT_MODEL_SELF / DEFAULT_MODEL_OTHER.
        self.observations = {}
        self.bump_models = {}
        self.min_obs_for_fit = min_obs_for_fit
        self.l2_reg = l2_reg
        self.obs_window = obs_window
        # `cost` is the all-in cost basis used by the bid optimizer (input + production
        # for intermediaries; production cost for producers). `production_cost` is the
        # value-add component used in the balance formula -- for intermediaries the
        # input cost is already deducted separately via the buyer-side payment, so the
        # balance update must only subtract the value-add (otherwise the input is
        # double-counted). Default: same as `cost`, which is correct for producers.
        self.cost = cost
        self.production_cost = production_cost if production_cost is not None else cost
        self.revenue_fn = revenue_fn if revenue_fn is not None else fixed(0)
        self.revenue_forecast_fn = revenue_forecast_fn if revenue_forecast_fn is not None else fixed(0)
        self.revenue_forecast = {}
        self.balance = 0.0
        # Two stock pools. `inventory` is FINISHED goods (ready to sell downstream).
        # `input_inventory` is RAW inputs (received from upstream, waiting to be
        # consumed when production starts). Only intermediaries that assemble use it.
        # input_inventory=None means "this agent does not track inputs separately" --
        # upstream deliveries land directly in `inventory`. That's right for:
        #   - top-tier producers (raw materials assumed available, no inputs delivered)
        #   - pure retailers / pass-through oems with production_time=0
        self.inventory = inventory
        self.input_inventory = input_inventory
        # Perishability. `shelf_life` is the maximum number of ticks a unit may sit
        # in a stock pool before it spoils and is written off (it expires once its
        # age reaches shelf_life, i.e. t - entry_time >= shelf_life). None means
        # non-perishable -- the age ledgers below are then never touched and the
        # scalar pools above are the whole story. When set, it applies to BOTH the
        # finished `inventory` and the raw `input_inventory` pools. Each ledger is a
        # FIFO deque of [entry_time, qty] batches; the scalar pool stays the
        # authoritative total (so every read-side projection is unchanged) and the
        # ledger only records how that total is distributed across ages. Initial
        # stock is seeded as a single batch entering at t=0. Spoilage is sunk-cost
        # only: production_cost was already charged at start_production, so a write-
        # off needs no further balance change -- the unit just disappears.
        self.shelf_life = shelf_life
        self._inv_batches = deque()
        self._input_batches = deque()
        if shelf_life is not None:
            if inventory > 0:
                self._inv_batches.append([0, inventory])
            if input_inventory is not None and input_inventory > 0:
                self._input_batches.append([0, input_inventory])
        self.safety_stock = safety_stock
        # Production state. `production_time` is tau (delay from start to completion).
        # `production_schedule` tracks in-progress production keyed by completion time.
        # `supply_fn(t)` is the START capacity per tick (units startable at time t),
        # NOT the per-tick deliverable supply. Deliverable supply comes from completions
        # via `production_schedule` + standing `inventory`.
        self.production_time = production_time
        self.production_schedule = {}  # {completion_time: qty}
        # How many ticks beyond t+tau to plan for when deciding build-to-stock starts.
        self.build_to_stock_horizon = build_to_stock_horizon
        # Running count of units started THIS tick, used to cap further starts at
        # supply_fn(t). Reset at the top of each tick. Tracks both build-to-stock
        # starts (from decide_production_starts) and make-to-order starts triggered
        # mid-trading by MTO-on-commit, so capacity is shared across both paths.
        self.starts_today = 0
        # EWMA of realised procurement prices, keyed by (upstream_supplier, lead_time).
        # Updated on each realised delivery as the BUYER (see delivery section of the
        # main loop). Used by replacement_cost() to price new-production offers.
        # upstream_suppliers is the list of upstream-layer agents this one can buy
        # from -- set at config time after all agents are constructed.
        self.cost_ewma = {}
        self.upstream_suppliers = []
        self.ewma_alpha = ewma_alpha
        self.cumulative_demand_forecast = {}
        self.cumulative_supply_forecast = {}
        self.cumulative_surplus_forecast = {}

    def _fill_window(self, target, t, window, value_fn, *, cumulative=False):
        # Shared loop body for every forecast in this class. Walks the planning
        # horizon [t, t+window] and writes one entry into `target` per tick.
        # Keys are absolute times (future_t = t + lead), not lead offsets, so
        # downstream lookups don't need to know which tick the forecast was built on.
        #
        #   target     the dict to populate (e.g. self.demand_forecast)
        #   t          current tick; the window starts here
        #   window     forecast_window; window+1 entries are written (inclusive)
        #   value_fn   called as value_fn(future_t) for each tick in the window
        #   cumulative if True, target[t+k] becomes the running sum of value_fn
        #              from t..t+k (used for the cumulative_* forecasts)
        running = 0
        for lead in range(window + 1):
            future_t = t + lead
            v = value_fn(future_t)
            if cumulative:
                running += v
                target[future_t] = running
            else:
                target[future_t] = v

    def predict_demand(self, t, forecast_window, contracts=None):
        # demand_forecast is keyed by INPUT-arrival time. End-customer-facing agents
        # have no transformation so demand_fn is used directly. For intermediaries an
        # obligation to deliver at T_out requires inputs at T_out - tau, so sold
        # contracts are matched by (future_t + production_time).
        if self.demand_fn is not None:
            observed = self.demand_fn(t)
            self._fill_window(self.demand_forecast, t, forecast_window, lambda _: observed)
        else:
            assert contracts is not None, "Intermediary needs contracts to derive demand"
            def obligation(future_t):
                ot = future_t + self.production_time
                return contracts.outbound_qty(self, ot, ot)
            self._fill_window(self.demand_forecast, t, forecast_window, obligation)

    def predict_revenue(self, t, forecast_window):
        observed = self.revenue_forecast_fn(t)
        self._fill_window(self.revenue_forecast, t, forecast_window, lambda _: observed)

    def update_cumulative_demand(self, t, forecast_window):
        self._fill_window(
            self.cumulative_demand_forecast, t, forecast_window,
            lambda ft: self.demand_forecast.get(ft, 0),
            cumulative=True,
        )

    def update_cumulative_supply(self, t, forecast_window, contracts):
        def inbound(ft):
            return contracts.inbound_qty(self, ft, ft)
        self._fill_window(
            self.cumulative_supply_forecast, t, forecast_window,
            inbound, cumulative=True,
        )

    def update_cumulative_surplus(self, t, forecast_window):
        self._fill_window(
            self.cumulative_surplus_forecast, t, forecast_window,
            lambda ft: (self.cumulative_supply_forecast.get(ft, 0)
                        - self.cumulative_demand_forecast.get(ft, 0)),
        )

    def refresh(self, t, forecast_window, contracts):
        # One-call refresh of forecast state from current contracts. Run at the top
        # of each tick and between trading layers, so newly-formed contracts'
        # derived demand becomes visible to the next layer up.
        self.predict_revenue(t, forecast_window)
        self.predict_demand(t, forecast_window, contracts)
        self.update_cumulative_demand(t, forecast_window)
        self.update_cumulative_supply(t, forecast_window, contracts)
        self.update_cumulative_surplus(t, forecast_window)

    def update_supply_forecast(self, delivery_time, delta):
        # Called on the BUYER when a contract is created/removed for delivery at delivery_time.
        for dt in self.cumulative_supply_forecast:
            if dt >= delivery_time:
                self.cumulative_supply_forecast[dt] += delta
                self.cumulative_surplus_forecast[dt] += delta

    def update_demand_forecast(self, delivery_time, delta):
        # Called on the SELLER when a contract is created/removed. delivery_time is
        # the contract's OUTPUT delivery time. For intermediaries the corresponding
        # INPUT demand bumps at delivery_time - production_time (the moment the input
        # is needed to start assembly). For end-customer-facing agents and producers
        # production_time is typically 0, so this collapses to delivery_time.
        input_demand_time = delivery_time - self.production_time
        self.demand_forecast[input_demand_time] = self.demand_forecast.get(input_demand_time, 0) + delta
        for dt in self.cumulative_demand_forecast:
            if dt >= input_demand_time:
                self.cumulative_demand_forecast[dt] += delta
                self.cumulative_surplus_forecast[dt] -= delta

    # ----- shared production / offer aggregates -----

    def _completions_through(self, T):
        # Total scheduled production completing on or before T.
        return sum(qty for ct, qty in self.production_schedule.items() if ct <= T)

    def _committed_outbound(self, lo, hi, contracts):
        # Units already promised for delivery in [lo, hi] with this agent as supplier.
        return contracts.outbound_qty(self, lo, hi)

    def _future_start_capacity(self, t, latest_start):
        # Units this agent could still START in [t, latest_start]: today's remaining
        # capacity (supply_fn(t) - starts_today, floored at 0) plus full capacity each
        # subsequent tick through latest_start. Caller must ensure supply_fn is set.
        cap = max(0, self.supply_fn(t) - self.starts_today)
        for s in range(t + 1, latest_start + 1):
            cap += self.supply_fn(s)
        return cap

    def current_input_stock(self):
        # The pool the agent draws from for its OWN consumption / production starts.
        # Intermediaries hold raw inputs in input_inventory; producers and pure
        # retailers track everything in inventory. The bidder-side projected-stock
        # check uses this because an intermediary bidding upstream cares about input
        # availability, not finished-goods availability.
        return self.input_inventory if self.input_inventory is not None else self.inventory

    def projected_stock(self, T, t, supply_adjustments=None):
        # Projected stock of the agent's relevant pool (input_inventory for
        # intermediaries that assemble, finished inventory otherwise) at delivery
        # time T, evaluated by clipping at 0 each tick so past-period shortfalls
        # are not carried forward.
        #
        # A delivery at T cannot retroactively serve a customer's missed demand at
        # T-k -- that demand is lost the instant it goes unmet. The naive
        # `inventory + (cumulative_supply - cumulative_demand)` formula treats
        # inventory as if it could go arbitrarily negative, which inflates the
        # buyer's perceived need and causes over-bidding for short-lead deliveries
        # that physically cannot recover the lost periods.
        #
        # supply_adjustments: optional {time: delta} applied to cumulative supply
        # for times >= the adjustment time. The bid stage uses this to ask "what
        # would my projection look like if I lost a specific existing contract?"
        # which is needed when the buyer is the incumbent on a unit being resold.
        inv = self.current_input_stock()
        prev_cs = 0
        prev_cd = 0
        for t_prime in range(t, T + 1):
            cs = self.cumulative_supply_forecast.get(t_prime, 0)
            if supply_adjustments:
                for adj_time, delta in supply_adjustments.items():
                    if adj_time <= t_prime:
                        cs += delta
            cd = self.cumulative_demand_forecast.get(t_prime, 0)
            supply_at_t = cs - prev_cs
            demand_at_t = cd - prev_cd
            inv = max(0, inv + supply_at_t - demand_at_t)
            prev_cs = cs
            prev_cd = cd
        return inv

    def record_observation(self, supplier, lead_frac, price, penalty, bump):
        # Append one outcome (lead_frac, price+penalty, bump) to the per-supplier
        # observation buffer. supplier=None records a self-observation (used by
        # the seller-side self-model). Called at delivery time for every contract
        # (bump=False on success, True on renege) and at bump time on both the
        # bumped buyer (about the seller) and the bumping seller (about self).
        buf = self.observations.get(supplier)
        if buf is None:
            buf = self.observations[supplier] = deque(maxlen=self.obs_window)
        buf.append((lead_frac, price + penalty, int(bump)))

    def fit_bump_model(self, supplier):
        # Refit the logistic model for one supplier (or self when supplier=None).
        # No-op until min_obs_for_fit observations exist -- before that the
        # cold-start default in model_params() is used instead. L2-regularised NLL
        # is convex, so L-BFGS-B from the previous fit (or zero) converges fast.
        obs = self.observations.get(supplier, [])
        if len(obs) < self.min_obs_for_fit:
            return
        X = np.array([[1.0, lead, stake] for lead, stake, _ in obs])
        y = np.array([bump for _, _, bump in obs], dtype=float)
        l2 = self.l2_reg

        def nll(w):
            z = X @ w
            return float(np.sum(np.logaddexp(0.0, z) - y * z)
                         + 0.5 * l2 * float(np.dot(w, w)))

        def grad(w):
            z = X @ w
            # expit is the numerically stable logistic sigmoid: the naive
            # 1/(1+exp(-z)) overflows when L-BFGS-B's line search pushes z large
            # and negative (exp(-z) -> inf). Same value, no RuntimeWarning.
            p = expit(z)
            return X.T @ (p - y) + l2 * w

        x0 = np.array(self.bump_models.get(supplier, (0.0, 0.0, 0.0)))
        result = minimize(nll, x0=x0, jac=grad, method='L-BFGS-B', bounds=BUMP_BOUNDS)
        if result.success:
            self.bump_models[supplier] = tuple(float(x) for x in result.x)

    def fit_all_bump_models(self):
        for supplier in list(self.observations):
            self.fit_bump_model(supplier)

    def model_params(self, supplier=None):
        # Fitted (w0, w_lead, w_stake) if one exists for this supplier; otherwise
        # the cold-start default. Self uses the no-stake-sensitivity default to
        # match the original C=0 self-belief; others use a mildly stake-sensitive
        # default to match the original C=0.001 buyer-side belief.
        if supplier in self.bump_models:
            return self.bump_models[supplier]
        return DEFAULT_MODEL_SELF if supplier is None else DEFAULT_MODEL_OTHER

    # ----- stock pools (perishability-aware mutation) -----
    # Every write to inventory / input_inventory routes through these so the scalar
    # total and the FIFO age ledger stay in lock-step. When shelf_life is None the
    # ledger is skipped and these collapse to plain scalar arithmetic, so a
    # non-perishable agent behaves exactly as before. Consumption is FIFO (oldest
    # batch first) so units are sold/used before they spoil -- the realistic policy
    # and the one that minimises waste.

    @staticmethod
    def _fifo_remove(batches, qty):
        # Drain `qty` from the front (oldest) of a batch deque. Tolerates float
        # quantities (retailer demand can be fractional). Leaves the deque empty
        # rather than going negative if qty exceeds the recorded total.
        remaining = qty
        while remaining > 0 and batches:
            entry_time, batch_qty = batches[0]
            if batch_qty > remaining:
                batches[0][1] = batch_qty - remaining
                remaining = 0
            else:
                remaining -= batch_qty
                batches.popleft()

    def add_inventory(self, qty, t):
        if qty <= 0:
            return
        self.inventory += qty
        if self.shelf_life is not None:
            self._inv_batches.append([t, qty])

    def consume_inventory(self, qty):
        if qty <= 0:
            return
        self.inventory -= qty
        if self.shelf_life is not None:
            self._fifo_remove(self._inv_batches, qty)

    def add_input(self, qty, t):
        if qty <= 0:
            return
        self.input_inventory += qty
        if self.shelf_life is not None:
            self._input_batches.append([t, qty])

    def consume_input(self, qty):
        if qty <= 0:
            return
        self.input_inventory -= qty
        if self.shelf_life is not None:
            self._fifo_remove(self._input_batches, qty)

    def _expire_pool(self, batches, t):
        # Remove and total up every batch old enough to spoil (age >= shelf_life).
        # Batches are append-ordered by entry_time, so all expired ones sit at the
        # front -- stop at the first survivor.
        spoiled = 0
        while batches and t - batches[0][0] >= self.shelf_life:
            spoiled += batches.popleft()[1]
        return spoiled

    def expire(self, t):
        # Write off spoiled stock in both pools. Returns (spoiled_inventory,
        # spoiled_input) so the caller can log it. No-op (returns zeros) when this
        # agent is non-perishable. Sunk-cost only: no balance adjustment here.
        if self.shelf_life is None:
            return 0, 0
        spoiled_inv = self._expire_pool(self._inv_batches, t)
        self.inventory -= spoiled_inv
        spoiled_input = 0
        if self.input_inventory is not None:
            spoiled_input = self._expire_pool(self._input_batches, t)
            self.input_inventory -= spoiled_input
        return spoiled_inv, spoiled_input

    # ----- production lifecycle -----

    def complete_production(self, t):
        # Move units whose scheduled completion time is t out of the schedule and
        # into finished inventory. Called once per tick, before production decisions
        # and trading so the trading auction sees today's completions in stock.
        completed = self.production_schedule.pop(t, 0)
        self.add_inventory(completed, t)
        return completed

    def start_production(self, qty, t):
        # Schedule qty units to complete at t + production_time. Capped first by
        # remaining start-capacity this tick (supply_fn(t) - starts_today), then by
        # input availability for intermediaries. Returns the actual quantity started
        # (may be less than requested). May be called multiple times in a tick:
        # once by decide_production_starts (build-to-stock) and again per new
        # contract by MTO-on-commit during trading -- the starts_today counter
        # shares capacity across both paths.
        # tau == 0 is treated as an instantaneous transformation: units go straight
        # into finished inventory rather than being scheduled for completion.
        #
        # Accounting: the value-add (production_cost) is deducted from balance HERE,
        # at production start, not at sale. This matches the auction's "excess stock =
        # cost 0" semantics: once a unit has been produced the cost is sunk, so the
        # optimizer correctly treats subsequent sales as having zero cost basis. The
        # delivery balance update therefore records only the sale price, not
        # (price - production_cost). Input cost for intermediaries is unaffected --
        # it's still charged when inputs are purchased on the buyer side.
        if qty <= 0:
            return 0
        if self.supply_fn is not None:
            remaining_capacity = max(0, self.supply_fn(t) - self.starts_today)
            qty = min(qty, remaining_capacity)
        if self.input_inventory is not None:
            qty = min(qty, self.input_inventory)
            self.consume_input(qty)
        if qty <= 0:
            return 0
        self.starts_today += qty
        self.balance -= qty * self.production_cost
        if self.production_time == 0:
            self.add_inventory(qty, t)
        else:
            completion_time = t + self.production_time
            self.production_schedule[completion_time] = (
                self.production_schedule.get(completion_time, 0) + qty
            )
        return qty

    def decide_production_starts(self, t, contracts):
        # Build-to-stock policy: across the planning window [t+tau, t+tau+horizon],
        # find the largest shortfall of projected finished inventory below safety_stock
        # and start that many units this tick (capped by start capacity and inputs).
        # Today's starts complete at t + tau, so they only help projections at or after
        # t + tau. Earlier shortfalls can't be fixed this tick (in-progress already
        # determines them).
        if self.supply_fn is None:
            return 0
        capacity = self.supply_fn(t)
        if capacity <= 0:
            return 0
        max_needed = 0
        for lead in range(self.production_time, self.production_time + self.build_to_stock_horizon + 1):
            future_t = t + lead
            outbound = self._committed_outbound(t, future_t, contracts)
            completions = self._completions_through(future_t)
            projected = self.inventory + completions - outbound
            shortfall = max(0, self.safety_stock - projected)
            max_needed = max(max_needed, shortfall)
        starts = min(max_needed, capacity)
        if self.input_inventory is not None:
            starts = min(starts, self.input_inventory)
        return starts

    # ----- chain-feasibility (Phase 4) -----

    def projected_capacity_throughput(self, T, t):
        # Upper-bound on units this agent can have available for delivery by T:
        #   current finished inventory
        # + already-scheduled production completions on or before T
        # + maximum NEW starts (today's remaining capacity + each subsequent tick's
        #   full capacity) that could complete by T (i.e. started no later than T-tau)
        # Used by can_chain_deliver to test feasibility of additional commitments
        # spanning multiple future ticks of production. Conservative against
        # build-to-stock variability -- BTS may start less than the cap.
        completions_by_T = self._completions_through(T)
        if self.supply_fn is None or T < t + self.production_time:
            return self.inventory + completions_by_T
        latest_start = T - self.production_time
        future_starts = self._future_start_capacity(t, latest_start)
        return self.inventory + completions_by_T + future_starts

    def can_chain_deliver(self, T, t, contracts, qty=1):
        # Recursive chain-feasibility test: can this agent fulfil qty additional
        # units at delivery T, given the current contracts list and projected
        # production / input flow up the chain?
        #
        # 1. Own throughput check: existing_outbound + qty <= projected_capacity.
        # 2. For intermediaries that consume inputs, count only the production
        #    starts NOT YET scheduled -- units already in finished inventory had
        #    their inputs consumed earlier, units already in production_schedule
        #    likewise. Only NEW starts (= shortfall between outbound and what's
        #    already produced/in-progress) need additional inputs by T-tau, and
        #    those must be satisfiable from current input_inventory + scheduled
        #    inbound. Any further shortfall recurses to upstream suppliers; if
        #    any one can chain-deliver it, the chain is feasible. (v1: doesn't
        #    split shortfall across multiple upstreams in parallel.)
        # 3. Top-tier producers have no upstream_suppliers, recursion terminates.
        projected = self.projected_capacity_throughput(T, t)
        existing_outbound = self._committed_outbound(t, T, contracts)
        if existing_outbound + qty > projected:
            return False
        if self.input_inventory is not None and self.upstream_suppliers:
            input_arrival = T - self.production_time
            if input_arrival < t:
                return False  # Can't get input before now
            # New production needed = outbound that isn't already covered by
            # finished inventory or in-progress production_schedule completions.
            completions_by_T = self._completions_through(T)
            new_production_needed = max(0, (existing_outbound + qty)
                                           - self.inventory - completions_by_T)
            if new_production_needed == 0:
                return True
            existing_inputs = contracts.inbound_qty(self, t, input_arrival)
            input_shortfall = max(0, new_production_needed - self.input_inventory - existing_inputs)
            if input_shortfall == 0:
                return True
            for upstream in self.upstream_suppliers:
                if upstream.can_chain_deliver(input_arrival, t, contracts, qty=input_shortfall):
                    return True
            return False
        return True

    # ----- replacement cost (EWMA) -----

    def update_ewma(self, supplier, lead, price):
        # Called on the BUYER side at delivery time when a unit is actually paid for.
        # Builds up the running average of realised procurement prices keyed by
        # (upstream_supplier, agreed_lead_time of the contract).
        key = (supplier, lead)
        if key in self.cost_ewma:
            self.cost_ewma[key] = self.ewma_alpha * price + (1 - self.ewma_alpha) * self.cost_ewma[key]
        else:
            self.cost_ewma[key] = price

    def lookup_ewma(self, supplier, lead, fallback=None):
        # Exact-key hit first; otherwise nearest known lead for the same supplier;
        # otherwise the configured fallback.
        if (supplier, lead) in self.cost_ewma:
            return self.cost_ewma[(supplier, lead)]
        known_leads = [l for (s, l) in self.cost_ewma if s is supplier]
        if known_leads:
            nearest = min(known_leads, key=lambda l: abs(l - lead))
            return self.cost_ewma[(supplier, nearest)]
        return fallback

    def replacement_cost(self, t, delivery_time):
        # Marginal cost of producing one new unit for delivery at delivery_time.
        # For top-tier producers (no upstream / no input pool): production only,
        # which equals their production cost.
        # For intermediaries: production_cost + cheapest learned upstream input price
        # at the relevant lead (the lead at which the input must arrive to start
        # assembly in time). Cold-start fallback: the static estimate already baked
        # into self.cost (cost - production_cost = expected input cost).
        if self.input_inventory is None or not self.upstream_suppliers:
            return self.production_cost
        input_arrival = delivery_time - self.production_time
        upstream_lead = max(0, input_arrival - t)
        fallback_input = max(0, self.cost - self.production_cost)
        costs = []
        for upstream in self.upstream_suppliers:
            est = self.lookup_ewma(upstream, upstream_lead, fallback=fallback_input)
            if est is not None:
                costs.append(est)
        if not costs:
            return self.production_cost + fallback_input
        return self.production_cost + min(costs)

    # ----- offer-stage split (excess vs new production) -----

    def offerable_units(self, delivery_time, t, contracts):
        # Splits this agent's offerable units at delivery_time into:
        #   excess     -- units backed by existing inventory or already-in-progress
        #                 production, beyond what's needed for committed outbound +
        #                 safety_stock. Reservation cost 0 (sunk input).
        #   new_prod   -- units producible fresh, drawing on start capacity at any
        #                 tick in [t, T-tau]. lead>tau commits don't trigger MTO on
        #                 commit -- BTS picks them up at a later tick -- so the same
        #                 capacity slot can't be sold twice within one auction. We
        #                 net out "soft commitments": outbound contracts in the
        #                 window not yet represented in production_schedule, which
        #                 still need a start slot in [t, T-tau]. lead==tau commits
        #                 DO trigger MTO immediately, so their production_schedule
        #                 entry cancels their outbound contribution -- they consume
        #                 starts_today via start_production instead.
        #                 Input check applies only at lead==tau: lead>tau commits
        #                 procure inputs via the upstream auction before production
        #                 actually starts; chain feasibility is verified at commit.
        #                 Reservation cost = replacement_cost(t, delivery_time).
        # Resale of existing committed contracts is handled separately by the auction
        # fallback when neither bucket has capacity.
        completions_by_T = self._completions_through(delivery_time)
        outbound_by_T = self._committed_outbound(t, delivery_time, contracts)
        atp = self.inventory + completions_by_T - outbound_by_T
        excess = max(0, atp - self.safety_stock)
        new_prod = 0
        if delivery_time >= t + self.production_time and self.supply_fn is not None:
            latest_start = delivery_time - self.production_time
            window_capacity = self._future_start_capacity(t, latest_start)
            earliest_delivery = t + self.production_time
            outbound_in_window = self._committed_outbound(earliest_delivery, delivery_time, contracts)
            completions_in_window = sum(qty for ct, qty in self.production_schedule.items()
                                        if earliest_delivery <= ct <= delivery_time)
            soft_commitments = max(0, outbound_in_window - completions_in_window)
            new_prod = max(0, window_capacity - soft_commitments)
            if self.input_inventory is not None and delivery_time == earliest_delivery:
                new_prod = min(new_prod, self.input_inventory)
        return excess, new_prod
