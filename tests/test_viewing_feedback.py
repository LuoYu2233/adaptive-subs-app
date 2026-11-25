from hashlib import sha256
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest

import numpy as np
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from viewing.model import Batch, DAY, Prior, fit, initial, likelihood, objective, parameters
from viewing.study import parse_session, report


def simulate(rng, index, words, digest, *, domain="科技", count=160):
    start = DAY * (1 + index)
    rows = []
    for k in range(count):
        w = rng.choice(list(words))
        b = words[w]["b_initial"]
        recorded = float(expit(2 * (b - 8.7)))
        shown = recorded >= .3 and rng.random() < .7   # some high-risk words hit a display cap
        p = float(expit(2 * (7.9 + (.20 if domain == "科技" else -.20) - b)))
        known, x = rng.random() < p, rng.random()
        if shown:
            event = "known" if known and x < .32 else "none"
        elif known:
            event = "unknown" if x < .005 else "lookup" if x < .025 else "none"
        else:
            event = "unknown" if x < .45 else "lookup" if x < .65 else "none"
        rows.append({"cue": k, "start": 0, "end": len(w), "word": w, "shown": shown, "event": event,
                     "at": start + k, "pUnknown": recorded})
    return {"version": 1, "kind": "word-viewing-study", "id": f"id-{index}", "clip": f"clip{index}",
            "domain": domain, "field": "b_initial", "tableSha256": digest, "started": start, "ended": start + count,
            "stopReason": "stopped", "exposures": rows}


class ViewingModelTests(unittest.TestCase):
    def test_emission_probabilities_sum_to_one_and_gradients_match_finite_differences(self):
        prior = Prior(8, .3, 2)
        x = np.array([8.4, .3, .1, -.7, -1., -.25])
        for shown, events in ((False, ["none", "unknown", "lookup"]), (True, ["none", "known"])):
            total = 0
            for event in events:
                sessions = [{"domain": "新闻", "rows": [{"b": 8.1, "shown": shown, "event": event, "at": 0}]}]
                data = Batch.build(sessions, ["新闻"])
                ll, _ = likelihood(x, data, prior)
                total += math.exp(ll)
                actual = objective(x, data, prior)[1]
                step = 1e-5
                expected = np.array([(objective(x + delta, data, prior)[0] - objective(x - delta, data, prior)[0]) / (2 * step)
                                     for delta in np.eye(len(x)) * step])
                np.testing.assert_allclose(actual, expected, atol=2e-7)
            self.assertAlmostEqual(total, 1)
        data = Batch.build([{"domain": "新闻", "rows": [{"b": 809., "shown": True, "event": "known", "at": 0}]}], ["新闻"])
        ll, grad = likelihood(x, data, prior)
        self.assertTrue(np.isfinite(ll) and np.all(np.isfinite(grad)))
        self.assertLess(ll, -1000)

    def test_age_weights_and_no_action_depend_on_display_condition(self):
        p = Prior(8, .3, 2)
        rows = [{"domain": "d", "rows": [{"b": 8, "shown": False, "event": "none", "at": 0},
            {"b": 8, "shown": True, "event": "none", "at": DAY}, {"b": 8, "shown": True, "event": "unknown", "at": DAY}]}]
        data = Batch.build(rows, ["d"], reference=DAY, half_life=1)
        self.assertEqual(data.included, 2)
        self.assertEqual(data.excluded, {"shown_unknown": 1})
        self.assertAlmostEqual(data.weight.sum(), 1.5)
        for shown, direction in ((False, 1), (True, -1)):
            rows[0]["rows"] = [{"b": 8, "shown": shown, "event": "none", "at": 0}] * 12
            batch = Batch.build(rows, ["d"])
            x, _ = fit(batch, p, domain_bias=False, viewing_discount=False, fit_feedback=False)
            self.assertGreater(direction * (x[0] - p.theta), 0)
            self.assertLess(abs(x[0] - p.theta), .3)
        empty = Batch.build([], ["d"])
        x, _ = fit(empty, p)
        np.testing.assert_allclose(x, initial(p, ["d"]), atol=1e-12)

    def test_later_policy_is_conditioned_on_actual_display_but_the_prior_remains_the_cat_anchor(self):
        p = Prior(8.7, .3, 2)
        words = {"reed": {"b_initial": 8}}
        study = simulate(random.Random(12), 2, words, "a" * 64, count=3)
        study["policy"] = {"source": "feedback", "anchorTheta": 8.7, "effectiveTheta": 7.5, "discrimination": 2,
                           "trainedThrough": DAY, "domain": "科技"}
        for row in study["exposures"]: row["pUnknown"] = float(expit(2 * (8 - 7.5)))
        self.assertEqual(len(parse_session(study, words, "a" * 64, "b_initial", p)["rows"]), 3)
        study["policy"]["trainedThrough"] = 10 * DAY
        with self.assertRaisesRegex(ValueError, "后来"):
            parse_session(study, words, "a" * 64, "b_initial", p)
        study["policy"]["trainedThrough"] = DAY
        study["policy"]["domain"] = "体育"
        with self.assertRaisesRegex(ValueError, "领域"):
            parse_session(study, words, "a" * 64, "b_initial", p)

    def test_frozen_future_videos_validation_and_paired_behavioral_scores(self):
        rng = random.Random(674)
        words = {f"w{i}": {"b_initial": 6.6 + i / 18, "b_frequency": 6.8 + i / 18} for i in range(70)}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            def save(name, data):
                path = base / name
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
                return path
            table = save("难度", {"version": 1, "scale": "natural-log lemma rank", "words": words})
            digest = sha256(table.read_bytes()).hexdigest()
            record = save("能力", {"version": 1, "model": "3PL word CAT", "fits": {"b_initial": {"theta": 8.7, "se": .3}},
                "parameters": {"discrimination": 2, "tableSha256": digest, "thetaByField": {"b_initial": 8.7}}})
            sessions = [simulate(rng, i, words, digest, domain=["新闻", "科技"][i % 2]) for i in range(30)]
            paths = [save(f"观看{i}", s) for i, s in enumerate(sessions)]
            def run(train, test, **opts): return report(record, table, "b_initial", train, test, **opts)
            result = run(paths[:20], paths[20:])
            self.assertEqual(result["heldOut"]["included"], 1600)
            self.assertLess(result["heldOut"]["updatedEventLogLoss"], result["heldOut"]["anchorEventLogLoss"] - .02)
            self.assertGreater(result["estimate"]["domainBias"]["科技"], result["estimate"]["domainBias"]["新闻"])
            self.assertLess(abs(result["estimate"]["watchingTheta"] - 7.9), .4)
            self.assertEqual(result, run(paths[19::-1], paths[:19:-1]))
            sessions[-1]["exposures"][0]["event"] = "none"
            save(paths[-1], sessions[-1])
            self.assertEqual(run(paths[:20], paths[20:])["estimate"], result["estimate"])
            with self.assertRaisesRegex(ValueError, "重复"):
                run(paths[:2], paths[:1])
            with self.assertRaisesRegex(ValueError, "时间"):
                run(paths[2:4], paths[:1])
            sessions[-1]["exposures"][0]["pUnknown"] = .123456
            save(paths[-1], sessions[-1])
            with self.assertRaisesRegex(ValueError, "试测参数"):
                run(paths[:2], paths[-1:])
            table.write_bytes(table.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "指纹"):
                run(paths[:2], paths[2:3])


if __name__ == "__main__":
    unittest.main()
