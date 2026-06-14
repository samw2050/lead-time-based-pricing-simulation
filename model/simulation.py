"""The Simulation: runs an N-tier supply-chain auction over discrete ticks."""

import numpy as np

from bump_model import bumped_prob
from contracts import contract, ContractBook
from solver import solve, solve_checked


class Simulation:
    """Runs an N-tier supply-chain auction simulation.

    Construct with an ordered list of Tier objects (top-of-chain first). The
    Simulation derives upstream_suppliers wiring, the trading_layers pair list,
    and the top-down delivery order from that list -- no manual wiring needed.

        sim = Simulation(tiers=[producers, intermediaries, retailers])
        sim.run()
    """
    def __init__(self, tiers, *, forecast_window=12, simulation_length=24,
                 unit_size=1, minimisation_tolerance=1e-4, verify_cache=False,
                 seed=None):
        if not tiers:
            raise ValueError("Simulation needs at least one tier")
        retailer_indices = [i for i, t in enumerate(tiers) if t.role == "retailer"]
        if len(retailer_indices) > 1:
            raise ValueError("At most one tier may have role='retailer'")
        if retailer_indices and retailer_indices[0] != len(tiers) - 1:
            raise ValueError("The 'retailer' tier must be the last tier in the chain")

        self.tiers = list(tiers)
        self.forecast_window = forecast_window
        self.simulation_length = simulation_length
        self.unit_size = unit_size
        self.minimisation_tolerance = minimisation_tolerance
        self.verify_cache = verify_cache
        # Per-auction processing order is shuffled with this RNG so identically
        # configured agents don't inherit a structural advantage from their list
        # position (the exact-tie tie-break otherwise always favours the first-
        # listed agent). seed=None => nondeterministic; pass a seed to reproduce.
        self.seed = seed
        self._rng = np.random.default_rng(seed)
        self.t = 0
        self.contracts = ContractBook()
        # Structured event log for the current tick, consumed by the GUI to drive
        # edge animations and the deliveries-vs-failures graph. Cleared at the top
        # of each tick(); appended to alongside the existing prints. Purely
        # additive -- nothing in the model reads it.
        self.events = []
        self.lead_times = list(range(forecast_window + 1))
        self._wire_upstream()

    def _wire_upstream(self):
        # Each non-top tier's agents draw inputs from the tier directly above.
        # Top-tier agents keep whatever upstream_suppliers they were constructed
        # with (default []), which is correct for raw producers.
        for i in range(1, len(self.tiers)):
            upstream_agents = self.tiers[i - 1].agents
            for a in self.tiers[i].agents:
                a.upstream_suppliers = list(upstream_agents)

    # ----- derived views -----

    @property
    def all_agents(self):
        return [a for tier in self.tiers for a in tier.agents]

    @property
    def trading_layers(self):
        # Adjacent (buyers, sellers) pairs, processed bottom-up each tick. Pull
        # semantics: the retailer tier's exogenous demand drives the lowest pair;
        # the contracts it generates become the next tier up's derived demand for
        # the next pair. Order matters -- if we ran the topmost pair first, the
        # upstream auction would decide before the downstream auction had created
        # any new derived demand for that tick, and the equilibrium would shift.
        return [(self.tiers[i + 1].agents, self.tiers[i].agents)
                for i in range(len(self.tiers) - 2, -1, -1)]

    @property
    def supplier_tiers(self):
        # Every tier except 'retailer'. These participate in the top-down
        # supplier-delivery loop after trading.
        return [t for t in self.tiers if t.role != "retailer"]

    @property
    def retailer_tier(self):
        for t in self.tiers:
            if t.role == "retailer":
                return t
        return None

    # ----- main loop -----

    def run(self):
        while self.t <= self.simulation_length:
            self.tick()
            self.t += 1
            solve.cache_clear()
        print("\n--- Final balances ---")
        for a in self.all_agents:
            print(f"  {a.name}: {a.balance:+.2f}")

    def tick(self):
        t = self.t
        contracts = self.contracts
        forecast_window = self.forecast_window
        self.events = []

        print(f"Month {t}")
        self._print_status()

        # --- forecasts (start of tick) ---
        for a in self.all_agents:
            a.refresh(t, forecast_window, contracts)

        # --- refit bump-probability models ---
        # Once per tick, before any auction in this tick consults model_params().
        # Each agent's per-supplier observation buffer was populated by the
        # previous tick's deliveries and any in-tick bump events.
        for a in self.all_agents:
            a.fit_all_bump_models()

        # --- reset per-tick production counters ---
        for a in self.all_agents:
            a.starts_today = 0

        # --- production completion ---
        # In-progress units whose scheduled completion is today move into finished
        # inventory. Done before production decisions so today's completions count
        # toward inventory when deciding whether to start more.
        for a in self.all_agents:
            a.complete_production(t)

        # --- spoilage ---
        # Write off stock that has exceeded its shelf life before it can be offered
        # this tick. Runs after completions (today's completions enter at t and so
        # cannot spoil the same tick unless shelf_life is 0) and before production
        # decisions / trading, so the auction never offers a unit that's about to be
        # discarded. Sunk-cost only -- the spoiled units just vanish.
        for a in self.all_agents:
            spoiled_inv, spoiled_input = a.expire(t)
            if spoiled_inv or spoiled_input:
                parts = []
                if spoiled_inv:
                    parts.append(f"{spoiled_inv:g} finished")
                if spoiled_input:
                    parts.append(f"{spoiled_input:g} raw")
                print(f"  [t={t}] {a.name} spoils {' + '.join(parts)} units")
                self.events.append({"type": "spoiled", "agent": a.name,
                                    "inventory": float(spoiled_inv),
                                    "input_inventory": float(spoiled_input)})

        # --- production decisions ---
        # Each producer/intermediary decides today's starts using its build-to-stock
        # policy. Today's starts immediately enter production_schedule (or, if
        # tau==0, directly inventory), so the trading auction below sees them via
        # offerable_units.
        for a in self.all_agents:
            starts = a.decide_production_starts(t, contracts)
            a.start_production(starts, t)

        # --- trading ---
        # For each future delivery time, run the per-layer auction bottom-up. Before
        # each layer, refresh the upcoming sellers' forecasts so their derived demand
        # (from contracts created in the just-finished layer) is current.
        for lead_time in self.lead_times:
            delivery_time = t + lead_time
            for buyer_layer, seller_layer in self.trading_layers:
                for a in seller_layer:
                    a.refresh(t, forecast_window, contracts)
                self._run_auction(buyer_layer, seller_layer, delivery_time, lead_time)

        # --- delivery ---
        # Top-down: each supplier tier delivers contracts due today; downstream
        # tiers receive deliveries before the next supplier tier runs. Then the
        # retailer tier (if any) serves end customers from finished inventory.
        for tier in self.supplier_tiers:
            self._deliver_supplier_layer(tier.agents)
        if self.retailer_tier is not None:
            for retailer in self.retailer_tier.agents:
                self._serve_end_customers(retailer)

        print(f"  [t={t}] balances: " + ", ".join(
            f"{a.name}: {a.balance:+.2f}" for a in self.all_agents))

        # --- prune settled contracts ---
        # Everything due on or before t has now been delivered or reneged, so it no
        # longer represents an outstanding commitment. Dropping it keeps the contract
        # indexes bounded to live contracts and makes supplier_load reflect only
        # outstanding obligations. (The GUI snapshot runs after t increments and
        # already filters to delivery_time >= t, so its view is unaffected.)
        self.contracts.prune(t)

    # ----- printing helpers -----

    def _print_status(self):
        def stock_str(a):
            parts = [f"inv:{a.inventory}"]
            if a.input_inventory is not None:
                parts.append(f"raw:{a.input_inventory}")
            if a.production_schedule:
                in_prog = sum(a.production_schedule.values())
                parts.append(f"wip:{in_prog}")
            return f"{a.name}=[" + ",".join(parts) + "]"
        # Pad tier names so the agent columns line up across rows.
        name_width = max(len(tier.name) for tier in self.tiers)
        for i, tier in enumerate(self.tiers):
            prefix = "  Stocks:" if i == 0 else "         "
            label = f"{tier.name}:".ljust(name_width + 1)
            print(f"{prefix} {label} {' '.join(stock_str(a) for a in tier.agents)}")
        print("  Trust (buyer -> [seller: P(success @ lead=mid, stake=0)]):")
        for buyers, sellers in self.trading_layers:
            for buyer in buyers:
                scores = ", ".join(
                    f"{s.name}:{1 - bumped_prob(buyer.model_params(s), 0.5, 0, 0):.3f}"
                    for s in sellers
                )
                print(f"    {buyer.name} -> [{scores}]")

    # ----- auction helpers -----

    @staticmethod
    def _seller_load(seller, contracts):
        """Total committed outbound quantity a seller is currently on the hook for.
        Used as a tie-break in conflict resolution so that work spreads evenly across
        sellers that submit identical bids -- the more heavily committed seller yields
        the tie. This is recomputed each auction round, so a seller that wins a unit
        becomes 'heavier' and the next tied award goes to its lighter rival, producing
        natural alternation instead of a permanent list-order bias."""
        return contracts.supplier_load(seller)

    # ----- auction: one adjacent-layer round at one delivery_time -----

    def _run_auction(self, buyers, sellers, delivery_time, lead_time):
        """Identical mechanics to the original 2-tier inner loop -- offer -> bid ->
        award -> negotiate -> conflict resolution -> commit, repeated until no more
        deals form. Works for any adjacent (buyers, sellers) pair in the chain."""
        t = self.t
        contracts = self.contracts
        unit_size = self.unit_size
        forecast_window = self.forecast_window
        minimisation_tolerance = self.minimisation_tolerance
        verify_cache = self.verify_cache
        lead_frac = lead_time / forecast_window

        # Shuffle the processing order so list position confers no advantage. These
        # are the tiers' canonical agent lists, so copy before shuffling -- never in
        # place. The exact-tie tie-break (bids/awards) and the sequential commit loop
        # would otherwise systematically favour the first-listed agent.
        buyers = list(buyers)
        self._rng.shuffle(buyers)
        sellers = list(sellers)
        self._rng.shuffle(sellers)

        # Aggregated across all rounds of the while loop for a single summary print
        # at the end -- one line per (supplier, customer) pair summarises everything
        # done at this (delivery, lead).
        bumped_from = {}
        repurchased = set()
        new_contracts_total = []
        # Phase 4: sellers whose can_chain_deliver check failed this auction. Excluded
        # from future rounds of the offer stage -- without this, a seller that's
        # chain-infeasible re-offers identically each round, fails again, and the
        # outer while loop never terminates. The exclusion is per-auction (resets
        # each call), which is correct because delivery_time is fixed across rounds.
        exhausted_sellers = set()

        while True:
            # --- offer stage ---
            # Each seller offers its cheapest available unit type this round. Phase 3
            # split: 'excess' (cost 0, from existing stock/in-progress) preferred over
            # 'new_prod' (cost = replacement_cost, requires starting fresh production).
            # If no seller has either, fall back to 'resale' of an existing committed
            # contract (cost 0 for the seller; resale_cost = incumbent.supplier_penalty
            # handles the bump burden).
            offers = {}  # seller -> {'incumbent': contract|None, 'cost': float, 'source': str}
            for s in sellers:
                if s in exhausted_sellers:
                    continue
                excess, new_prod = s.offerable_units(delivery_time, t, contracts)
                if excess >= unit_size:
                    offers[s] = {'incumbent': None, 'cost': 0.0, 'source': 'excess'}
                elif new_prod >= unit_size:
                    offers[s] = {'incumbent': None,
                                 'cost': s.replacement_cost(t, delivery_time),
                                 'source': 'new_prod'}
            if not offers:
                for c in contracts.by_delivery_time(delivery_time):
                    if (c.supplier in sellers
                            and c.supplier not in exhausted_sellers
                            and c.supplier not in offers):
                        offers[c.supplier] = {'incumbent': c, 'cost': 0.0, 'source': 'resale'}
            if not offers:
                break

            # --- bidding stage ---
            bids = {s: {} for s in offers}
            for seller, offer in offers.items():
                incumbent = offer['incumbent']
                seller_cost = offer['cost']
                # The bump penalty the SELLER perceives is scaled by its own
                # penalty_scale (>1 = more reluctant to bump). The bumped customer
                # still receives the unscaled penalty at commit/delivery.
                resale_cost = (incumbent.supplier_penalty * seller.penalty_scale
                               if incumbent is not None else 0)
                for buyer in buyers:
                    # Clipped projection: walks t..delivery_time applying max(0, ...) each
                    # step so past shortfalls don't carry forward as continuing need. Without
                    # this, cumulative_demand sums demand the buyer can't actually serve from
                    # this delivery, and the buyer over-bids for short-lead supply.
                    # The incumbent-resale case is encoded as a hypothetical loss of the
                    # buyer's existing contract at incumbent.delivery_time.
                    supply_adjustments = None
                    if incumbent is not None and buyer is incumbent.customer:
                        supply_adjustments = {incumbent.delivery_time: -incumbent.quantity}
                    projected = buyer.projected_stock(delivery_time, t, supply_adjustments=supply_adjustments)
                    if projected >= buyer.safety_stock:
                        continue
                    buyer_belief = buyer.model_params(seller)
                    seller_belief = seller.model_params()
                    rev = buyer.revenue_forecast.get(delivery_time, 0)

                    args = ('bid', seller_cost, buyer_belief, seller_belief,
                            lead_frac, rev, resale_cost, 0.0,
                            seller.min_penalty, seller.max_penalty, minimisation_tolerance)
                    bid = solve_checked(args, buyer, seller, t, lead_time, verify_cache=verify_cache)

                    if bid is not None:
                        bids[seller][buyer] = {'price': bid[0], 'supplier_penalty': bid[1], 'ev_s': bid[2]}

            # --- award stage ---
            awards = {}
            for seller, offer in offers.items():
                incumbent = offer['incumbent']
                seller_cost = offer['cost']
                ranked = sorted(bids[seller].items(), key=lambda item: item[1]['ev_s'], reverse=True)
                if not ranked:
                    continue
                if incumbent is not None and all(b is incumbent.customer for b, _ in ranked):
                    continue
                winner_buyer, winning_bid = ranked[0]
                # Seller reservation floor for the negotiation stage. Floored at 0 so
                # competition between buyers can never drag the seller below its own
                # break-even: with multiple bidders the runner-up's ev_s may itself be
                # negative in a loss-making market, and without this max() the
                # negotiated constraint ev_s >= second_ev_s would clear a below-cost
                # contract. New production (cost = replacement_cost) is therefore
                # refused unless price >= cost; sunk-cost excess/perishable offers
                # (cost 0) still clear, since their ev_s >= 0 regardless.
                second_ev_s = max(0.0, ranked[1][1]['ev_s'] if len(ranked) > 1 else 0.0)
                if incumbent is not None:
                    # Resale floor uses the same per-offer cost basis the bid optimizer
                    # is using this round (0 for resale, since the unit physically exists).
                    # The penalty term is scaled by the seller's penalty_scale so the
                    # reservation value matches the seller's perceived bump cost above.
                    # Reservation = forgone incumbent margin only; the penalty cost is
                    # already deducted inside EV_s (via resale_cost in the solver), so
                    # adding it here a second time would double-count it.
                    resale_floor = (incumbent.price - seller_cost)
                    second_ev_s = max(second_ev_s, resale_floor)
                awards[seller] = {
                    'winner': winner_buyer,
                    'winning_bid': winning_bid,
                    'second_ev_s': second_ev_s,
                    'incumbent': incumbent,
                }

            # --- negotiation stage ---
            negotiated = {}
            for seller, award in awards.items():
                buyer = award['winner']
                second_ev_s = award['second_ev_s']
                incumbent = award['incumbent']
                seller_cost = offers[seller]['cost']
                resale_cost = (incumbent.supplier_penalty * seller.penalty_scale
                               if incumbent is not None else 0)
                buyer_belief = buyer.model_params(seller)
                seller_belief = seller.model_params()
                rev = buyer.revenue_forecast.get(delivery_time, 0)

                args = ('negotiation', seller_cost, buyer_belief, seller_belief,
                        lead_frac, rev, resale_cost, second_ev_s,
                        seller.min_penalty, seller.max_penalty, minimisation_tolerance)
                neg = solve_checked(args, buyer, seller, t, lead_time, verify_cache=verify_cache)

                if neg is not None:
                    negotiated[seller] = {
                        'buyer': buyer,
                        'price': neg[0],
                        'supplier_penalty': neg[1],
                        'ev_c': neg[2],
                        'ev_s': neg[3],
                        'incumbent': incumbent,
                    }

            # --- conflict resolution ---
            # A buyer can be the top pick of several sellers in the same round; keep
            # only the offer that's best for that buyer (highest ev_c). Ties in ev_c
            # -- e.g. between identically-configured sellers whose bids match to the
            # bit -- are broken toward the seller carrying the lighter committed-
            # outbound load, so symmetric sellers share the work rather than the
            # earlier-listed one always winning. The (ev_c, -load) tuple only lets
            # load matter when ev_c is an exact tie; any real bid difference still
            # decides the winner on its own.
            wins_by_buyer = {}
            for seller, outcome in negotiated.items():
                wins_by_buyer.setdefault(outcome['buyer'], []).append((seller, outcome))
            for buyer, wins in wins_by_buyer.items():
                if len(wins) > 1:
                    wins.sort(key=lambda w: (w[1]['ev_c'], -self._seller_load(w[0], contracts)),
                              reverse=True)
                    for seller, _ in wins[1:]:
                        del negotiated[seller]

            if not negotiated:
                break

            # --- commit ---
            # Phase 4: each non-resale provisional award goes through can_chain_deliver
            # before commit. Resale commits are net-zero outbound changes so they don't
            # add to the chain feasibility burden. On failure the seller is dropped and
            # the while loop's next round will re-offer (alternative-seller retry via
            # the existing release/re-round path). The contracts list is mutated by
            # earlier iterations within this loop so the per-seller check sees prior
            # in-round commits as already-existing -- this prevents within-round over-
            # commitment at the seller's own layer (but not transitively at upstreams).
            # The check is done in dict-insertion order, which is the order sellers
            # appeared from offers and negotiated stages -- deterministic and stable.
            for seller, outcome in list(negotiated.items()):
                incumbent = outcome['incumbent']
                if incumbent is None and not seller.can_chain_deliver(delivery_time, t, contracts):
                    del negotiated[seller]
                    exhausted_sellers.add(seller)
                    continue
                if incumbent is not None:
                    if outcome['buyer'] is not incumbent.customer:
                        seller.balance -= incumbent.supplier_penalty * incumbent.quantity
                        incumbent.customer.balance += incumbent.supplier_penalty * incumbent.quantity
                        incumbent.customer.update_supply_forecast(delivery_time, -incumbent.quantity)
                        bumped_from[(seller, outcome['buyer'])] = incumbent.customer.name
                        # Bump = failed commitment for the incumbent contract. Record
                        # from both sides using the incumbent's own price/penalty/lead.
                        inc_lead_frac = incumbent.agreed_lead_time / forecast_window
                        incumbent.customer.record_observation(
                            seller, inc_lead_frac, incumbent.price,
                            incumbent.supplier_penalty, bump=True)
                        seller.record_observation(
                            None, inc_lead_frac, incumbent.price,
                            incumbent.supplier_penalty, bump=True)
                    else:
                        repurchased.add((seller, outcome['buyer']))
                    # The incumbent contract is being replaced -- drop the demand it placed
                    # on the seller before adding the new contract's demand below.
                    seller.update_demand_forecast(delivery_time, -incumbent.quantity)
                    contracts.remove(incumbent)

                c = contract()
                c.supplier = seller
                c.customer = outcome['buyer']
                c.delivery_time = delivery_time
                c.price = outcome['price']
                c.supplier_penalty = outcome['supplier_penalty']
                c.quantity = unit_size
                c.agreed_lead_time = lead_time
                contracts.add(c)
                new_contracts_total.append(c)
                outcome['buyer'].update_supply_forecast(delivery_time, unit_size)
                seller.update_demand_forecast(delivery_time, unit_size)

                # MTO-on-commit: only fire for 'new_prod' offers and only when this
                # contract requires starting today (lead == tau). 'excess' commits draw
                # from existing inventory/in-progress so no new start is needed;
                # 'resale' commits reallocate a physically-existing unit, also no start.
                # Contracts at lead > tau are deferred to subsequent ticks' BTS; lead <
                # tau is infeasible for new production and never reaches this branch
                # (offerable_units gates new_prod at lead >= tau).
                if (offers[seller]['source'] == 'new_prod'
                        and delivery_time - seller.production_time == t):
                    seller.start_production(unit_size, t)

        # --- summary print (once per layer/delivery_time, aggregated across all rounds) ---
        new_by_pair = {}
        for c in new_contracts_total:
            new_by_pair.setdefault((c.supplier, c.customer), []).append(c)
        for (seller, buyer), cs in new_by_pair.items():
            total_qty = sum(c.quantity for c in cs)
            avg_price = sum(c.price for c in cs) / len(cs)
            avg_penalty = sum(c.supplier_penalty for c in cs) / len(cs)
            bumped = bumped_from.get((seller, buyer))
            self.events.append({"type": "contract", "seller": seller.name,
                                "buyer": buyer.name, "qty": total_qty,
                                "penalty": avg_penalty,
                                "delivery_time": delivery_time})
            if bumped:
                print(f"  [t={t} lead={lead_time}] {seller.name} will bump {bumped} to supply {buyer.name} {total_qty} units "
                      f"@ avg price {avg_price:.2f}, avg penalty {avg_penalty:.2f} (delivery t={delivery_time})")
                self.events.append({"type": "bumped", "seller": seller.name,
                                    "buyer": buyer.name, "dropped": bumped, "qty": total_qty})
            elif (seller, buyer) in repurchased:
                print(f"  [t={t} lead={lead_time}] {buyer.name} repurchases {total_qty} units from {seller.name} "
                      f"@ avg price {avg_price:.2f}, avg penalty {avg_penalty:.2f} (delivery t={delivery_time})")
            else:
                print(f"  [t={t} lead={lead_time}] {seller.name} will supply {buyer.name} {total_qty} units "
                      f"@ avg price {avg_price:.2f}, avg penalty {avg_penalty:.2f} (delivery t={delivery_time})")

    # ----- delivery for a single supplier tier (called top-down) -----

    def _deliver_supplier_layer(self, supplier_layer):
        """Honour contracts due today for one supplier tier. Highest price+penalty
        contracts are honoured first when capacity is short; the rest are reneged
        and trigger penalty payouts + trust degradation. Downstream customers
        receive deliveries into either input_inventory (if they assemble) or
        finished inventory (if they're pass-through)."""
        t = self.t
        contracts = self.contracts

        delivered_qty_by_customer = {}
        due_by_supplier = {}
        for c in contracts.by_delivery_time(t):
            if c.supplier in supplier_layer:
                due_by_supplier.setdefault(c.supplier, []).append(c)

        for supplier, due_contracts in due_by_supplier.items():
            total_committed = sum(c.quantity for c in due_contracts)
            # In Phase 2 all producing agents pipeline through inventory (production
            # completes -> inventory -> delivery). So the deliverable amount today is
            # always the current finished inventory, regardless of supply_fn. supply_fn
            # is now START capacity, not delivery capacity.
            capacity = supplier.inventory

            if total_committed <= capacity:
                delivered = due_contracts
                reneged = []
            else:
                # Honour highest price+penalty contracts first
                ranked = sorted(due_contracts, key=lambda c: c.price + c.supplier_penalty, reverse=True)
                delivered = []
                reneged = []
                remaining = capacity
                for c in ranked:
                    if remaining >= c.quantity:
                        delivered.append(c)
                        remaining -= c.quantity
                    else:
                        reneged.append(c)

            delivered_by_customer = {}
            for c in delivered:
                delivered_by_customer.setdefault(c.customer, []).append(c)
            reneged_by_customer = {}
            for c in reneged:
                reneged_by_customer.setdefault(c.customer, []).append(c)
            for cust, cs in delivered_by_customer.items():
                qty = sum(c.quantity for c in cs)
                print(f"  [t={t}] {supplier.name} delivers {qty} units to {cust.name}")
                self.events.append({"type": "delivered", "supplier": supplier.name,
                                    "customer": cust.name, "qty": qty})
            for cust, cs in reneged_by_customer.items():
                qty = sum(c.quantity for c in cs)
                print(f"  [t={t}] {supplier.name} FAILS to deliver {qty} units to {cust.name}")
                self.events.append({"type": "failed", "supplier": supplier.name,
                                    "customer": cust.name, "qty": qty})

            for c in delivered:
                delivered_qty_by_customer[c.customer] = delivered_qty_by_customer.get(c.customer, 0) + c.quantity
                # Revenue only -- the value-add (production_cost) was already deducted
                # in start_production when this unit was produced. Net per unit is
                # still (sale_price - production_cost - any input cost), just split
                # across the production-start and delivery events.
                supplier.balance += c.price * c.quantity
                c.customer.balance -= c.price * c.quantity
                # EWMA update: the buyer learns the realised procurement price at the
                # lead it negotiated. Used by replacement_cost() to price future new-
                # production offers from this same upstream.
                c.customer.update_ewma(c.supplier, c.agreed_lead_time, c.price)
                lf = c.agreed_lead_time / self.forecast_window
                c.customer.record_observation(c.supplier, lf, c.price, c.supplier_penalty, bump=False)
                c.supplier.record_observation(None, lf, c.price, c.supplier_penalty, bump=False)

            for c in reneged:
                supplier.balance -= c.supplier_penalty * c.quantity
                c.customer.balance += c.supplier_penalty * c.quantity
                lf = c.agreed_lead_time / self.forecast_window
                c.customer.record_observation(c.supplier, lf, c.price, c.supplier_penalty, bump=True)
                c.supplier.record_observation(None, lf, c.price, c.supplier_penalty, bump=True)

            # Drain finished inventory by what was actually delivered.
            supplier.consume_inventory(sum(c.quantity for c in delivered))

        # Customers receive deliveries before the next supplier layer runs. For
        # intermediaries that assemble (input_inventory tracked separately), inbound
        # units land in input_inventory and only become finished stock once production
        # consumes and completes them. For pass-through receivers, they go straight
        # to finished inventory.
        for cust, qty in delivered_qty_by_customer.items():
            if cust.input_inventory is not None:
                cust.add_input(qty, t)
            else:
                cust.add_inventory(qty, t)

    # ----- end-customer service for retailers -----

    def _serve_end_customers(self, retailer):
        """A retailer fulfils exogenous demand from finished inventory. Shortfall
        is lost demand (no penalty, just unmet customers)."""
        t = self.t
        demand = retailer.demand_forecast.get(t, 0)
        delivered_to_customer = min(retailer.inventory, demand)
        shortfall = demand - delivered_to_customer
        retailer.consume_inventory(delivered_to_customer)
        retailer.balance += retailer.revenue_fn(t) * delivered_to_customer
        if delivered_to_customer > 0:
            print(f"  [t={t}] {retailer.name} delivers {delivered_to_customer:.0f} units to customers")
            self.events.append({"type": "delivered", "supplier": retailer.name,
                                "customer": "end", "qty": delivered_to_customer})
        if shortfall > 0:
            print(f"  [t={t}] {retailer.name} FAILS to deliver {shortfall:.0f} units to customers")
            self.events.append({"type": "failed", "supplier": retailer.name,
                                "customer": "end", "qty": shortfall})
