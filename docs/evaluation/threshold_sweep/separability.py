# -*- coding: utf-8 -*-
"""How separable are fault windows from healthy telemetry, under the trained detectors?"""
import io, json, sys, statistics as st
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('sweep_results.json'))
faults = [f for f in d['faults'] if f['score'] is not None]
normal = d['healthy']['normal']
recov = d['healthy']['recovery']

cross = [f['score'] for f in faults if f['cross']]
same = [f['score'] for f in faults if not f['cross']]


def auc(pos, neg):
    """Mann-Whitney U / ROC AUC: P(a random positive scores above a random negative)."""
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def desc(name, xs):
    print('%-28s n=%3d  mean=%+.5f  median=%+.5f  min=%+.5f  max=%+.5f'
          % (name, len(xs), st.mean(xs), st.median(xs), min(xs), max(xs)))


print('SCORE DISTRIBUTIONS (higher = more anomalous)')
desc('fault windows (all)', [f['score'] for f in faults])
desc('  same-service faults', same)
desc('  cross-service faults', cross)
desc('healthy: normal phase', normal)
desc('healthy: recovery phase', recov)
print()

print('SEPARABILITY (ROC AUC; 0.5 = indistinguishable, 1.0 = perfect)')
print('  fault vs healthy-normal      : %.3f' % auc([f['score'] for f in faults], normal))
print('  fault vs healthy-recovery    : %.3f' % auc([f['score'] for f in faults], recov))
print('  fault vs all healthy         : %.3f' % auc([f['score'] for f in faults], normal + recov))
print('  CROSS-service vs all healthy : %.3f' % auc(cross, normal + recov))
print('  same-service  vs all healthy : %.3f' % auc(same, normal + recov))
print()

best = None
for t, det, cr, ans, cor, fpn, fpr in d['sweep']:
    tpr = det / 48
    fp = (fpn + fpr) / 96
    youden = tpr - fp
    if best is None or youden > best[1]:
        best = ((t, det, cr, ans, cor, fpn, fpr), youden)
print('BEST OPERATING POINT by Youden J (TPR - FPR):')
t, det, cr, ans, cor, fpn, fpr = best[0]
print('  threshold %.4f -> detect %d/48 (%.1f%%), cross %d/24, answered %d, '
      'FP %d/48 normal + %d/48 recovery, J = %.3f'
      % (t, det, 100 * det / 48, cr, ans, fpn, fpr, best[1]))
print()
print('  default threshold 0.0000 -> detect 10/48 (20.8%), cross 4/24, '
      'FP 2/48 normal + 13/48 recovery')
