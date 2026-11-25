"""MAP fit for the event observed under each actual gloss display decision."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, log_softmax

DAY = 86_400_000
EVENT = {"none": 0, "unknown": 1, "lookup": 2, "known": 3}
EPSILON = np.array([.975, .005, .020])     # unglossed known word: no action, mark unknown, lookup
HIDDEN_PRIOR = np.array([3.5, 1., .5])    # Dirichlet parameters minus one, same order
SHOWN_PRIOR = np.array([4.5, .5])         # Beta parameters minus one: no action, mark known
DOMAIN_SD = .35
DISCOUNT_MEAN, DISCOUNT_SD = .15, .25


@dataclass(frozen=True)
class Prior:
    theta: float
    se: float
    slope: float


@dataclass
class Batch:
    domains: list
    b: np.ndarray
    domain: np.ndarray
    shown: np.ndarray
    event: np.ndarray
    weight: np.ndarray
    included: int
    excluded: dict

    @classmethod
    def build(cls, sessions, domains, *, reference=None, half_life=90):
        domain_indices = {domain: i for i, domain in enumerate(domains)}
        grouped = defaultdict(list)
        excluded = Counter()
        included = 0
        for session in sessions:
            for row in session["rows"]:
                shown, event = row["shown"], row["event"]
                if (shown and event not in ("none", "known")) or (not shown and event == "known"):
                    excluded[("shown" if shown else "hidden") + "_" + event] += 1
                    continue
                weight = 1. if reference is None else 2. ** (-((reference - row["at"]) / DAY) / half_life)
                key = (row["b"], domain_indices.get(session["domain"], -1), shown, EVENT[event])
                grouped[key].append(weight)
                included += 1
        keys = sorted(grouped)
        return cls(list(domains), np.array([k[0] for k in keys], dtype=float), np.array([k[1] for k in keys], dtype=int),
                   np.array([k[2] for k in keys], dtype=bool), np.array([k[3] for k in keys], dtype=int),
                   np.array([math.fsum(grouped[k]) for k in keys]), included, dict(sorted(excluded.items())))


def initial(prior, domains):
    return np.r_[prior.theta, DISCOUNT_MEAN, np.log(HIDDEN_PRIOR[1:] / HIDDEN_PRIOR[0]),
                 np.log(SHOWN_PRIOR[1] / SHOWN_PRIOR[0]), np.zeros(len(domains))]


def likelihood(x, data, prior):
    """Return weighted log likelihood and analytic gradient. New domains have zero bias."""
    theta, discount, mark_logit, lookup_logit, known_logit = x[:5]
    domain_bias = np.r_[x[5:], 0.][data.domain]
    eta = prior.slope * (theta + domain_bias - discount - data.b)
    log_hidden = log_softmax([0., mark_logit, lookup_logit])
    log_shown = log_softmax([0., known_logit])
    hidden, shown = ~data.shown, data.shown
    event = data.event
    # Compute in log space so a confidently wrong prediction still has finite loss.
    emit_known = np.empty_like(eta)
    emit_unknown = np.empty_like(eta)
    emit_known[hidden] = np.log(EPSILON[event[hidden]])
    emit_unknown[hidden] = log_hidden[event[hidden]]
    emit_known[shown] = log_shown[(event[shown] == EVENT["known"]).astype(int)]
    emit_unknown[shown] = np.where(event[shown] == EVENT["known"], -np.inf, 0.)
    known_part = -np.logaddexp(0., -eta) + emit_known
    unknown_part = -np.logaddexp(0., eta) + emit_unknown
    evidence = np.logaddexp(known_part, unknown_part)
    known_after = np.exp(known_part - evidence)
    unknown_after = np.exp(unknown_part - evidence)
    residual = data.weight * prior.slope * (known_after - expit(eta))
    grad = np.zeros_like(x)
    grad[0], grad[1] = residual.sum(), -residual.sum()
    assigned = data.domain >= 0
    grad[5:] = np.bincount(data.domain[assigned], weights=residual[assigned], minlength=len(x) - 5)
    u = data.weight[hidden] * unknown_after[hidden]
    for offset in (1, 2):
        grad[1 + offset] = u @ ((event[hidden] == offset) - np.exp(log_hidden[offset]))
    k = data.weight[shown] * known_after[shown]
    grad[4] = k @ ((event[shown] == EVENT["known"]) - np.exp(log_shown[1]))
    return data.weight @ evidence, grad


def objective(x, data, prior):
    ll, grad = likelihood(x, data, prior)
    scales = np.r_[prior.se, DISCOUNT_SD, np.full(len(x) - 5, DOMAIN_SD)]
    offsets = np.r_[x[0] - prior.theta, x[1] - DISCOUNT_MEAN, x[5:]]
    normalized = offsets / scales
    ll -= .5 * (normalized @ normalized)
    grad[np.r_[0:2, 5:len(x)]] -= normalized / scales
    log_hidden = log_softmax([0., *x[2:4]])
    log_shown = log_softmax([0., x[4]])
    ll += HIDDEN_PRIOR @ log_hidden + SHOWN_PRIOR @ log_shown
    grad[2:4] += HIDDEN_PRIOR[1:] - HIDDEN_PRIOR.sum() * np.exp(log_hidden[1:])
    grad[4] += SHOWN_PRIOR[1] - SHOWN_PRIOR.sum() * np.exp(log_shown[1])
    return -ll, -grad


def fit(data, prior, *, domain_bias=True, viewing_discount=True, fit_feedback=True):
    x0 = initial(prior, data.domains)
    bounds = [(None, None)] * len(x0)
    if viewing_discount:
        bounds[1] = (0., None)
    else:
        x0[1] = 0.; bounds[1] = (0., 0.)
    if not domain_bias:
        bounds[5:] = [(0., 0.)] * len(data.domains)
    if not fit_feedback:
        for i in (2, 3, 4):
            bounds[i] = (x0[i], x0[i])
    result = minimize(objective, x0, args=(data, prior), jac=True, method="L-BFGS-B", bounds=bounds,
                      options={"ftol": 1e-13, "gtol": 1e-7, "maxiter": 600, "maxls": 40})
    if not result.success or not np.isfinite(result.fun) or not np.all(np.isfinite(result.x)):
        raise ValueError("反馈模型未收敛：" + str(result.message))
    return result.x, int(result.nit)


def parameters(x, domains):
    _, mark, lookup = np.exp(log_softmax([0., *x[2:4]]))
    return {"theta": float(x[0]), "viewingDiscount": float(x[1]), "watchingTheta": float(x[0] - x[1]),
            "domainBias": dict(zip(domains, map(float, x[5:]))),
            "feedback": {"markUnknown": float(mark), "lookup": float(lookup), "markKnown": float(expit(x[4]))}}


def score(x, data, prior):
    ll, _ = likelihood(x, data, prior)
    total = float(data.weight.sum())
    return {"included": data.included, "excluded": data.excluded, "weight": total,
            "eventLogLoss": float(-ll / total) if total else None}
