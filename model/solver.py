"""Two-stage contract solver (bid and negotiation stages).

The bid and negotiation stages are duals of one another; each reduces to a small
constrained optimisation over (price, penalty) given buyer/seller beliefs.
`solve` is the memoised entry point; `solve_checked` wraps it with an optional
cache-correctness check.
"""

from functools import cache
from scipy.optimize import minimize

from bump_model import bumped_prob


def _solve_impl(mode, cost, buyer_belief, seller_belief, lead_frac, rev,
                resale_cost, seller_floor, min_penalty, max_penalty, tol):
    # Two-stage contract solver. The bid and negotiation stages are duals:
    #
    #   mode='bid'         -- seller maximises EV_s subject to buyer's EV_b >= 0
    #                         (buyer must at least break even to bid).
    #   mode='negotiation' -- buyer maximises EV_b subject to seller's EV_s >= floor
    #                         (seller must beat its second-best alternative).
    #
    # EV_b = (1 - p_buyer)  * (rev - price)   + p_buyer  * penalty
    # EV_s = (1 - p_seller) * (price - cost)  - p_seller * penalty - resale_cost
    #
    # `seller_floor` is unused (0.0) for the bid mode; for negotiation it's the
    # next-best EV_s the seller could have taken instead.
    #
    # The penalty is constrained to the seller's [min_penalty, max_penalty] window
    # (max_penalty=None => unbounded above). Both parties negotiate price and
    # penalty within it; if no feasible point exists inside the window, the solver
    # returns None and no contract forms.
    def ev_b(price, penalty):
        p = bumped_prob(buyer_belief, lead_frac, price, penalty)
        return (1 - p) * (rev - price) + p * penalty
    def ev_s(price, penalty):
        p = bumped_prob(seller_belief, lead_frac, price, penalty)
        return (1 - p) * (price - cost) - p * penalty - resale_cost

    # Seed the penalty guess inside the window so x0 is feasible when min_penalty > 0.
    if mode == 'bid':
        objective = lambda x: -ev_s(*x)
        constraint = lambda x: ev_b(*x)
        x0 = [rev / 2, min_penalty]
    else:  # 'negotiation'
        objective = lambda x: -ev_b(*x)
        constraint = lambda x: ev_s(*x) - seller_floor
        x0 = [rev, min_penalty]

    result = minimize(
        objective, x0=x0, method='SLSQP',
        bounds=[(0.0, rev), (min_penalty, max_penalty)],
        constraints={'type': 'ineq', 'fun': constraint},
        options={'ftol': tol},
    )
    if not (result.success and constraint(result.x) >= -tol):
        return None
    price, penalty = result.x
    if mode == 'bid':
        return (price, penalty, ev_s(price, penalty))
    return (price, penalty, ev_b(price, penalty), ev_s(price, penalty))


solve = cache(_solve_impl)


def solve_checked(args, buyer, seller, t, lead_time, verify_cache=False):
    # Wraps the cached solve with an optional debug check. When verify_cache is
    # on, also runs the uncached _solve_impl on the same args and warns if the
    # two disagree -- which would mean some piece of state that affects the
    # result isn't part of the cache key. No-op in normal runs.
    result = solve(*args)
    if verify_cache:
        fresh = _solve_impl(*args)
        if fresh != result:
            print(f"  [CACHE WARNING] {args[0]} key incomplete for "
                  f"{buyer.name}/{seller.name} at t={t} lead={lead_time}")
    return result
