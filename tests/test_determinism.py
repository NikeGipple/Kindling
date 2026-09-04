"""Determinismo del baseline **tra processi**, non solo dentro lo stesso.

Il test gemello in ``test_metrics.py`` confronta due chiamate nello stesso
processo, e il primo difetto di determinismo di questo layer — ``hash()`` di una
tupla di stringhe, che Python randomizza a ogni processo — era invisibile
esattamente a quella forma: dentro un processo l'hash e' stabile, e il test
passava mentre due esecuzioni del job davano numeri diversi.

Qui il calcolo viene rieseguito in sottoprocessi con ``PYTHONHASHSEED`` diverso.
Non serve a scoprire un bug, serve a tenere vero un invariante che vale oggi:
``modularity_random_mean`` e ``modularity_z`` non devono dipendere da niente che
cambi da un'esecuzione all'altra, perche' ``is_significant`` e' un flag
pubblicato e la stabilita' tra snapshot misurerebbe altrimenti il rumore
dell'algoritmo invece del cambiamento della community.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Ripetizioni basse: qui interessa che il numero sia lo STESSO, non che sia
# stabile in senso statistico — e ogni sottoprocesso paga l'import di igraph.
PROBE = """
import json
from datetime import datetime, timezone
from job.config import LAYER_VOICE, MetricParams
from job.edges import Edge
from job.metrics import build_metrics

T0 = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)

def e(a, b, w=10.0):
    lo, hi = (a, b) if a < b else (b, a)
    return Edge(layer=LAYER_VOICE, src_author_id=lo, dst_author_id=hi, weight=w,
                weight_undecayed=w, raw_units=w, interaction_count=4,
                last_interaction_at=T0)

edges = []
for group in (list(range(1, 8)), list(range(100, 107))):
    for i, a in enumerate(group):
        for b in group[i + 1:]:
            edges.append(e(a, b))
edges.append(e(7, 100, 0.5))

params = MetricParams(min_nodes_structural=4, baseline_repetitions=12,
                      min_modularity_z=1.0)

# Da build_metrics e non da compute_communities: e' l'orchestratore a derivare i
# semi dei generatori (_seed_for), ed e' proprio li' che viveva il difetto —
# hash() di una tupla di stringhe, stabile dentro un processo e diverso tra due.
result = build_metrics(snapshot_id=1, guild_id=1, as_of=T0, params=params,
                       edges=edges)
row = result.communities[0]
rob = result.robustness[0]
print(json.dumps({
    "modularity": row.modularity,
    "modularity_random_mean": row.modularity_random_mean,
    "modularity_random_sd": row.modularity_random_sd,
    "modularity_z": row.modularity_z,
    "is_significant": row.is_significant,
    "targeted_excess": rob.targeted_excess,
    "targeted_z": rob.targeted_z,
}))
"""


def _run_with_hash_seed(seed: str) -> dict:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        pytest.fail(f"sottoprocesso PYTHONHASHSEED={seed} fallito:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_baseline_identico_tra_processi_con_hash_seed_diversi():
    valori = [_run_with_hash_seed(seed) for seed in ("0", "1", "12345")]

    primo = valori[0]
    for altro in valori[1:]:
        assert altro == primo

    # Non un caso degenere in cui tutto e' None e il confronto e' banale.
    assert primo["modularity_random_mean"] is not None
    assert primo["modularity_z"] is not None
