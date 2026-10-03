import json, math, random, tempfile, unittest
from pathlib import Path
from quant import evaluate_signal as ev, freeze
from quant.signals import ALL


def synthetic(n=600, seed=4):
    """Market mid is imperfect; 'good' carries the missing information, 'noise' carries none."""
    rng = random.Random(seed); rows = []
    for i in range(n):
        truth = rng.gauss(0, 1.2); hidden = rng.gauss(0, 1)
        p_true = 1 / (1 + math.exp(-(truth + hidden))); pm = 1 / (1 + math.exp(-truth))
        rows.append(dict(ticker='M%d' % i, t=float(i), p_market=pm, y=int(rng.random() < p_true),
                         features=dict(good=hidden, noise=rng.gauss(0, 1), prob=p_true)))
    return rows


class EvaluateTests(unittest.TestCase):
    def test_informative_signal_beats_market_and_noise_does_not(self):
        rows = synthetic()
        good, noise = ev.evaluate_feature(rows, 'good'), ev.evaluate_feature(rows, 'noise')
        self.assertGreater(good['improvement'], 0.02); self.assertLess(noise['improvement'], 0.01)
        self.assertGreater(good['corr_residual'], 0.2); self.assertEqual(len(good['blocks']), 4)
        self.assertTrue(all(b['train'] < b['train'] + b['test'] for b in good['blocks']))
    def test_walk_forward_trains_only_on_the_past(self):
        r = ev.evaluate_feature(synthetic(), 'good')
        self.assertEqual([b['train'] for b in r['blocks']], [120, 240, 360, 480])
    def test_probability_signal_reports_raw_log_loss(self):
        r = ev.evaluate_feature(synthetic(), 'prob', prob=True)
        self.assertLess(r['ll_signal_prob_all'], r['ll_market_all'])
    def test_duplicate_calls_rejected_and_small_sample_flagged(self):
        rows = synthetic(60); rows.append(dict(rows[0]))
        with self.assertRaises(ValueError): ev.evaluate_feature(rows, 'good')
        self.assertTrue(ev.evaluate_feature(synthetic(20), 'good')['status'].startswith('insufficient'))
    def test_evaluate_all_requires_frozen_registry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'r.json'
            with self.assertRaises(freeze.FrozenMismatch): ev.evaluate_all([], registry=p)
            freeze.write(p); out = ev.evaluate_all([], registry=p)
            self.assertEqual(set(out), {s.NAME for s in ALL})


class FreezeTests(unittest.TestCase):
    def test_never_overwrites_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'r.json'; freeze.write(p); freeze.verify(p)
            with self.assertRaises(freeze.FrozenMismatch): freeze.write(p)
            doc = json.loads(p.read_text()); doc['signals'][0]['sha256'] = '0' * 64; p.write_text(json.dumps(doc))
            with self.assertRaises(freeze.FrozenMismatch): freeze.verify(p)
    def test_committed_registry_matches_current_code(self):
        freeze.verify()  # fails if any signal source changed after the committed freeze


if __name__ == '__main__':
    unittest.main()
