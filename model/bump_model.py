"""Logistic bump-probability model.

A fitted logistic predicts P(a contract is reneged / bumped out) from its lead
fraction and stake (price + penalty). `bumped_prob` evaluates such a model; this
module also seeds the cold-start defaults used before any observations exist.
"""

import math
import numpy as np


def _fit_logistic_to_clamped_linear(A, B, C, n_grid=11):
    # Least-squares fit of (w0, w_lead, w_stake) so that
    # sigmoid(w0 + w_lead*lead + w_stake*stake) ~= clamp(A + B*lead - C*stake, 0, 1)
    # on a small grid. Run once at import time to seed cold-start defaults that
    # behave like the previous static A/B/C beliefs before any observations exist.
    leads = np.linspace(0.0, 1.0, n_grid)
    stakes = np.linspace(0.0, 200.0, n_grid)
    LL, SS = np.meshgrid(leads, stakes, indexing='ij')
    p = np.clip(A + B * LL - C * SS, 1e-3, 1.0 - 1e-3)
    z = np.log(p / (1 - p))
    X = np.column_stack([np.ones(LL.size), LL.ravel(), SS.ravel()])
    w, *_ = np.linalg.lstsq(X, z.ravel(), rcond=None)
    return tuple(float(x) for x in w)


# Cold-start logistic params used until a (buyer, supplier) pair has accumulated
# min_obs_for_fit observations. The asymmetry between self (no stake sensitivity)
# and others (small stake sensitivity) mirrors the previous A/B/C defaults of
# {0.1, 0.5, 0.0} for self and {0.1, 0.5, 0.001} for others.
DEFAULT_MODEL_SELF = _fit_logistic_to_clamped_linear(A=0.1, B=0.5, C=0.0)
DEFAULT_MODEL_OTHER = _fit_logistic_to_clamped_linear(A=0.1, B=0.5, C=0.001)


def bumped_prob(model_params, lead_frac, price, penalty):
    # Bumped-probability estimate from a fitted logistic model. model_params is
    # the (w0, w_lead, w_stake) tuple returned by agent.model_params(supplier).
    # Sigmoid is bounded in (0, 1) by construction, so no clamp is needed and the
    # function is smooth -- helps the SLSQP bid/negotiation solver converge.
    # The two-branch form is the numerically stable sigmoid: SLSQP can explore
    # large |z| during line search and a naive 1/(1+exp(-z)) overflows there.
    w0, w_lead, w_stake = model_params
    z = w0 + w_lead * lead_frac + w_stake * (price + penalty)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)
