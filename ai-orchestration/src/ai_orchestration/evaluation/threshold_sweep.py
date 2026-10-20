# -*- coding: utf-8 -*-
"""Anomaly-threshold sweep.

Scores every Evaluation incident's fault window, and every healthy (normal / recovery) phase
record, with the already-trained per-service detectors. Then sweeps ANOMALY_SCORE_THRESHOLD and
reports, at each operating point: detection coverage, cross-service coverage, the negative-control
false-positive rates, and how many detected incidents the deterministic signature matcher would
actually answer correctly.

Nothing is retrained and nothing is invented: the models, the incidents and the signature
thresholds are exactly those used in the reported run.
"""
import io, json, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from ai_orchestration.anomaly.detector import AnomalyDetector
from ai_orchestration.anomaly import feature_extractor
from ai_orchestration.evaluation import replay
from ai_orchestration.rca import signatures
from dataset_tools import config as dt_config

SERVICES = ['auth-service', 'payment-service', 'ledger-service', 'notification-service']
DET = {s: AnomalyDetector.load(s) for s in SERVICES}

incidents = replay.load_evaluation_incidents()
print('evaluation incidents: %d' % len(incidents))


def score(record):
    fv = feature_extractor.extract_features(record)
    if fv is None:
        return None
    d = DET.get(fv.service)
    if d is None or not d.is_trained:
        return None
    return d.score(fv).anomaly_score


def make_record(service, metrics, incident_id):
    return {'timestamp': None, 'service': service, 'correlation_id': incident_id,
            'health': {'status': 'UP', 'components': {}}, 'metrics': dict(metrics),
            'fault': None, 'collection_error': None}


# ---- 1. fault-window scores (one per incident, symptom service) -------------
faults = []
for inc in incidents:
    rec = replay.incident_to_raw_record(inc)
    s = score(rec)
    gt = replay.ground_truth(inc)
    # would the deterministic signature matcher get it right, if detection fired?
    fv = feature_extractor.extract_features(rec)
    matches = signatures.match_signatures(rec['service'], fv.raw_metrics) if fv else []
    sig_ok = bool(matches) and matches[0].fault_type == gt['fault_type']
    faults.append({'id': inc['incident_id'], 'score': s,
                   'cross': gt['is_cross_service'], 'sig_ok': sig_ok,
                   'matched': bool(matches), 'fault_type': gt['fault_type']})

# ---- 2. healthy-phase scores (negative control) -----------------------------
# Mirror negative_control.run() exactly: the SYMPTOM service only, one record per incident
# per phase, so the false-positive counts are directly comparable to the published Table VII.
healthy = {'normal': [], 'recovery': []}
for phase, key in (('normal', 'baseline_metrics'), ('recovery', 'recovery_metrics')):
    for inc in incidents:
        svc = inc.get('symptom_service')
        metrics = (inc.get(key) or {}).get(svc)
        if not svc or not metrics:
            continue
        s = score(make_record(svc, metrics, inc['incident_id']))
        if s is not None:
            healthy[phase].append(s)

print('fault-window scores : %d  (none-scored: %d)'
      % (sum(1 for f in faults if f['score'] is not None),
         sum(1 for f in faults if f['score'] is None)))
print('healthy normal      : %d' % len(healthy['normal']))
print('healthy recovery    : %d' % len(healthy['recovery']))
print()

scored = [f for f in faults if f['score'] is not None]
print('fault-window score range: %.6f .. %.6f' % (min(f['score'] for f in scored),
                                                  max(f['score'] for f in scored)))
print('healthy score range     : %.6f .. %.6f' % (min(healthy['normal'] + healthy['recovery']),
                                                  max(healthy['normal'] + healthy['recovery'])))
print()

# ---- 3. sweep ---------------------------------------------------------------
n_cross = sum(1 for f in faults if f['cross'])
print('cross-service incidents in the Evaluation set: %d of %d' % (n_cross, len(faults)))
print()

hdr = ('%-10s %8s %8s %10s %10s %10s %10s' %
       ('thresh', 'detect', 'cross', 'answered', 'correct', 'FP normal', 'FP recov'))
print(hdr); print('-' * len(hdr))

rows = []
for t in [-0.02, -0.01, -0.005, 0.0, 0.002, 0.004, 0.005, 0.006, 0.008, 0.010,
          0.012, 0.015, 0.020, 0.025, 0.030]:
    det = [f for f in scored if f['score'] > t]
    cross = [f for f in det if f['cross']]
    answered = [f for f in det if f['matched']]
    correct = [f for f in det if f['sig_ok']]
    fpn = sum(1 for s in healthy['normal'] if s > t)
    fpr = sum(1 for s in healthy['recovery'] if s > t)
    rows.append((t, len(det), len(cross), len(answered), len(correct), fpn, fpr))
    print('%-10.4f %8s %8s %10s %10s %10s %10s' % (
        t,
        '%d/%d' % (len(det), len(faults)),
        '%d/%d' % (len(cross), n_cross),
        '%d' % len(answered),
        '%d' % len(correct),
        '%d/%d' % (fpn, len(healthy['normal'])),
        '%d/%d' % (fpr, len(healthy['recovery']))))

json.dump({'faults': faults, 'healthy': healthy, 'sweep': rows},
          open('sweep_results.json', 'w'), indent=1)
print()
print('written: sweep_results.json')
