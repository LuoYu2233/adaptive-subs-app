"""Paired, seeded simulation using the actual word table. No human records or network access."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit

from viewing.model import Batch, DAY, Prior, fit, initial


def correction_fit(prior, batch):
    """The ablation treats corrective marks as if they were a representative knowledge test."""
    picked = (batch.event == 1) | (batch.event == 3)
    if not picked.any():
        return prior.theta
    target, b, w = batch.event[picked] == 3, batch.b[picked], batch.weight[picked]
    def loss(theta):
        logit = prior.slope * (theta - b)
        return .5 * ((theta - prior.theta) / prior.se) ** 2 + w @ np.logaddexp(0., np.where(target, -logit, logit))
    return float(minimize_scalar(loss, bracket=(prior.theta - .1, prior.theta + .1)).x)


def generate(rng, table, true_theta, prior, activity, clips, persistent):
    domains = ["科技", "日常"]
    biases = np.array([-.22, .22])
    rates = {"active": (.40, .16, .22), "quiet": (.10, .045, .055)}[activity]
    latent = rng.random(len(table)) if persistent else None
    overrides = set()
    result = []
    for i in range(clips):
        d = i % 2
        ranks = np.clip(np.exp(rng.normal(math.log(4000), 1.15, 120)).astype(int), 1, len(table))
        repeat = Counter()
        rows, gold = [], []
        for offset in range(0, len(ranks), 4):
            words = [int(rank - 1) for rank in ranks[offset:offset + 4] if rank - 1 not in overrides]
            eligible = [j for j, w in enumerate(words) if expit(prior.slope * (table[w] - prior.theta)) >= .3]
            shown_positions = set()
            for j in sorted(eligible, key=lambda j: -table[words[j]]):
                w = words[j]
                if repeat[w] < 2 and len(shown_positions) < 3:
                    shown_positions.add(j)
                    repeat[w] += 1
            for j, w in enumerate(words):
                shown, b = j in shown_positions, table[w]
                p = expit(prior.slope * (true_theta + biases[d] - b))
                known = (latent[w] if persistent else rng.random()) < p
                u = rng.random()
                if shown:
                    action = "known" if known and u < rates[2] else "none"
                else:
                    m, lookup = (.005, .02) if known else rates[:2]
                    action = "unknown" if u < m else "lookup" if u < m + lookup else "none"
                if action in ("known", "unknown"):
                    overrides.add(w)
                rows.append({"b": b, "at": i * DAY, "event": action, "shown": shown})
                gold.append((b, d, known))
        result.append({"domain": domains[d], "rows": rows, "gold": gold})
    return result, domains, biases


def evaluate(rows, theta, domain_bias, true_theta, true_bias):
    b, d, known = zip(*rows)
    d = np.asarray(d, dtype=int)
    eta = 2 * (theta + domain_bias[d] - b)
    return {"abilitySquaredError": float(np.mean((theta + domain_bias - true_theta - true_bias) ** 2)),
            "knowledgeLogLoss": float(np.mean(np.logaddexp(0., np.where(known, -eta, eta)))),
            "brier": float(np.mean((expit(eta) - known) ** 2))}


def benchmark(table_path, count, lengths, seed, persistent=True):
    if count < 2 or not lengths or min(lengths) < 1 or len(set(lengths)) != len(lengths):
        raise ValueError("至少需要两名模拟学习者及互不重复的正数训练长度")
    raw = Path(table_path).read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    # Sampling by frequency rank and fitting on the composite difficulty uses both real table columns.
    words = sorted(data["words"].values(), key=lambda r: r["rank"])
    table = np.array([r["b_initial"] for r in words], dtype=float)
    rng = np.random.default_rng(seed)
    results = defaultdict(list)
    for i in range(count):
        true_theta = rng.uniform(math.log(2500), math.log(10000))
        prior = Prior(true_theta + rng.normal(.35, .32), .30, 2.)
        activity = "active" if i % 2 else "quiet"
        clips, domains, true_bias = generate(rng, table, true_theta, prior, activity, max(lengths) + 4, persistent)
        for size in lengths:
            batch = Batch.build(clips[:size], domains, reference=size * DAY)
            gold = [r for clip in clips[size:size + 4] for r in clip["gold"]]
            corrected = correction_fit(prior, batch)
            base = initial(prior, domains)
            base[1] = 0
            mark = base.copy(); mark[0] = corrected
            fixed, _ = fit(batch, prior, fit_feedback=False)
            fitted, _ = fit(batch, prior)
            for label, x in (("CAT", base), ("correctionsOnly", mark), ("fixedFeedback", fixed), ("joint", fitted)):
                scores = evaluate(gold, x[0] - x[1], x[5:], true_theta, true_bias)
                results[(size, activity, label)].append(scores)

    def summary(rows):
        return {"participants": len(rows),
                "watchingThetaRMSE": math.sqrt(np.mean([r["abilitySquaredError"] for r in rows])),
                **{key: float(np.mean([r[key] for r in rows])) for key in ("knowledgeLogLoss", "brier")}}
    report = []
    for size in lengths:
        for activity in ("all", "quiet", "active"):
            grouped = {}
            for label in ("CAT", "correctionsOnly", "fixedFeedback", "joint"):
                arms = ("quiet", "active") if activity == "all" else (activity,)
                grouped[label] = summary([r for arm in arms for r in results[(size, arm, label)]])
            report.append({"trainingClips": size, "activity": activity, "models": grouped})
    return {"kind": "synthetic", "seed": seed, "participants": count, "tableSha256": sha256(raw).hexdigest(),
            "persistentWordKnowledge": persistent, "heldOutClipsPerFit": 4, "results": report}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("table", type=Path)
    ap.add_argument("--learners", default=30, type=int)
    ap.add_argument("--training-clips", default=[5, 20, 80], type=int, nargs="+")
    ap.add_argument("--seed", type=int, default=20261125)
    ap.add_argument("--independent-encounters", action="store_true")
    args = ap.parse_args()
    result = benchmark(args.table, args.learners, args.training_clips, args.seed, not args.independent_encounters)
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
