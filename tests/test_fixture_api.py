"""Il fixture non puo' divergere dal job senza che qualcosa fallisca.

``tools/fixture_api.py`` istanzia i modelli veri, quindi la FORMA e' garantita.
Il CONTENUTO no: tre volte i suoi valori sono stati impossibili per il job pur
restando validi per i modelli (codici di motivo inventati, ``nodes_removed`` con
un'aritmetica diversa, bucket che ``size_buckets()`` non emette). Questi test
verificano due cose distinte:

1. che gli scenari rispettino le regole del job (``problemi_di_contenuto`` vuoto);
2. che ogni controllo di contenuto **fallisca davvero** sulla divergenza per cui
   esiste. Un controllo verificato solo sul caso buono e' un controllo di cui non
   si sa niente: potrebbe non guardare nulla e passare lo stesso (CLAUDE.md 7).
"""

from __future__ import annotations

import copy
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from api.models import CommunitySizeBucket, CommunitySizeValues, Quality, RobustnessValues
from tools import fixture_api
from tools.fixture_api import (
    GUILD_EDGE,
    GUILD_MATURE,
    GUILD_TODAY,
    PARAMS,
    REMOVAL_FRACTIONS,
    SCENARIOS,
    nodes_removed_for,
    problemi_di_contenuto,
)


def _scenari():
    return copy.deepcopy(SCENARIOS)


def _sostituisci_valori(riga, **campi):
    return riga.model_copy(update={"values": riga.values.model_copy(update=campi)})


# --- gli scenari sono coerenti con il job ----------------------------------


def test_check_passa():
    assert fixture_api.check() == 0


def test_nessun_problema_di_contenuto():
    assert problemi_di_contenuto(SCENARIOS) == []


def test_la_formula_di_nodes_removed_e_quella_del_job():
    # job/robustness.py: max(1, ceil(X * n)). Mai zero.
    assert nodes_removed_for(14, 0.05) == 1
    assert nodes_removed_for(10, 0.05) == nodes_removed_for(10, 0.10) == 1
    assert nodes_removed_for(10, 0.20) == 2
    assert nodes_removed_for(61, 0.10) == 7
    for scenario in SCENARIOS.values():
        for r in scenario["robustness"]:
            if not r.quality.suppressed:
                assert r.values.nodes_removed >= 1


def test_le_costanti_vengono_dal_job():
    from job.config import ALL_LAYERS, MetricParams

    assert fixture_api.LAYERS == ALL_LAYERS
    assert REMOVAL_FRACTIONS == MetricParams().removal_fractions


# --- ogni controllo fallisce sulla divergenza per cui esiste ----------------


def _prima_pubblicata(scenari, gid):
    return next(i for i, r in enumerate(scenari[gid]["robustness"]) if not r.quality.suppressed)


def test_coglie_nodes_removed_con_l_aritmetica_vecchia():
    scenari = _scenari()
    righe = scenari[GUILD_TODAY]["robustness"]
    i = next(i for i, r in enumerate(righe) if r.layer == "voice" and r.removal_fraction == 0.05)
    n = righe[i].quality.n_effective
    righe[i] = _sostituisci_valori(righe[i], nodes_removed=max(0, int(n * 0.05)))

    assert any("nodes_removed" in p for p in problemi_di_contenuto(scenari))


def test_coglie_la_soppressione_mista_dentro_un_layer():
    scenari = _scenari()
    righe = scenari[GUILD_EDGE]["robustness"]
    i = next(i for i, r in enumerate(righe) if r.layer == "voice")
    righe[i] = righe[i].model_copy(update={
        "quality": Quality(suppressed=True, suppression_reason="below_threshold", details={}),
        "values": RobustnessValues(),
    })

    assert any("soppressione mista" in p for p in problemi_di_contenuto(scenari))


def test_coglie_un_n_diverso_tra_robustezza_e_community():
    scenari = _scenari()
    comm = scenari[GUILD_EDGE]["communities"]
    i = next(i for i, c in enumerate(comm) if c.layer == "voice")
    comm[i] = comm[i].model_copy(update={"quality": comm[i].quality.model_copy(update={"n_effective": 60})})

    assert any("n diverso dalla robustezza" in p for p in problemi_di_contenuto(scenari))


def test_coglie_un_layer_presente_in_un_solo_endpoint():
    scenari = _scenari()
    scenari[GUILD_EDGE]["communities"] = [
        c for c in scenari[GUILD_EDGE]["communities"] if c.layer != "mention"
    ]

    assert any("una sola tra robustezza e community" in p for p in problemi_di_contenuto(scenari))


def test_coglie_gap_diversi_nello_stesso_snapshot():
    # La divergenza della versione precedente di ...003: voice a 1,2 giorni e gli
    # altri layer a 7 dallo STESSO snapshot 76.
    scenari = _scenari()
    comm = scenari[GUILD_EDGE]["communities"]
    i = next(i for i, c in enumerate(comm) if c.layer == "reaction")
    comm[i] = _sostituisci_valori(comm[i], previous_gap_days=7.0)

    assert any("gap diversi" in p for p in problemi_di_contenuto(scenari))


def _community(scenari, gid, sid, layer):
    comm = scenari[gid]["communities"]
    return comm, next(i for i, c in enumerate(comm) if c.snapshot_id == sid and c.layer == layer)


def test_coglie_i_campi_del_confronto_in_parte_none():
    # La vista riconosce "nessun precedente" da previous_gap_days None: un gap
    # tolto lasciando overlap e conteggi descrive una riga che il job non scrive.
    scenari = _scenari()
    comm, i = _community(scenari, GUILD_TODAY, 12, "mention")
    comm[i] = _sostituisci_valori(comm[i], previous_gap_days=None)
    assert any("campi del confronto in parte None" in p for p in problemi_di_contenuto(scenari))

    # E il viceversa: un overlap su una riga senza precedente.
    scenari = _scenari()
    comm, i = _community(scenari, GUILD_TODAY, 11, "voice")
    comm[i] = _sostituisci_valori(comm[i], node_overlap=0.6)
    assert any("campi del confronto in parte None" in p for p in problemi_di_contenuto(scenari))


def test_coglie_stabilita_incoerente_con_la_sovrapposizione():
    # Stabilita' senza overlap al minimo...
    scenari = _scenari()
    comm, i = _community(scenari, GUILD_TODAY, 12, "mention")
    comm[i] = _sostituisci_valori(comm[i], stability_jaccard=0.4)
    assert any("stability_jaccard=0.4" in p for p in problemi_di_contenuto(scenari))
    # ...e overlap al minimo senza stabilita'.
    scenari = _scenari()
    comm, i = _community(scenari, GUILD_TODAY, 12, "voice")
    comm[i] = _sostituisci_valori(comm[i], stability_jaccard=None)
    assert any("stability_jaccard=None" in p for p in problemi_di_contenuto(scenari))


def test_coglie_una_sovrapposizione_impossibile_per_le_dimensioni():
    # La divergenza trovata il 16/09 su …002: 0,7 tra 33 e 12 nodi.
    scenari = _scenari()
    comm, i = _community(scenari, GUILD_TODAY, 12, "voice")  # 15 nodi contro 9: tetto 0,6
    comm[i] = _sostituisci_valori(comm[i], node_overlap=0.61)
    assert any("impossibile tra 15 e 9 nodi" in p for p in problemi_di_contenuto(scenari))


def test_coglie_una_sovrapposizione_con_il_layer_assente_nel_precedente():
    scenari = _scenari()
    comm, i = _community(scenari, GUILD_MATURE, 36, "voice")
    comm[i] = _sostituisci_valori(comm[i], node_overlap=0.2)
    assert any("assente nel precedente" in p for p in problemi_di_contenuto(scenari))


def test_coglie_un_codice_di_confrontabilita_inventato():
    scenari = fixture_api.SCENARIOS
    comm, i = _community(scenari, GUILD_MATURE, 29, "reply")
    originale = comm[i]
    try:
        details = {**originale.quality.details, "stability_unavailable": "parametri_diversi"}
        comm[i] = originale.model_copy(update={"quality": originale.quality.model_copy(update={"details": details})})
        assert "codice non presente nel sorgente del job: 'parametri_diversi'" in fixture_api._codici_estranei_al_job()
    finally:
        comm[i] = originale


def _con_bucket(scenari, gid, layer, trasforma):
    comm = scenari[gid]["communities"]
    i = next(i for i, c in enumerate(comm) if c.layer == layer)
    comm[i] = comm[i].model_copy(update={"sizes": trasforma(list(comm[i].sizes))})


def test_coglie_i_cinque_difetti_dei_bucket():
    # 1. n_effective valorizzato sul bucket
    scenari = _scenari()
    _con_bucket(scenari, GUILD_EDGE, "voice", lambda s: [
        b.model_copy(update={"quality": b.quality.model_copy(update={"n_effective": b.values.member_count})})
        for b in s
    ])
    assert any("quality.n_effective valorizzato" in p for p in problemi_di_contenuto(scenari))

    # 2. bucket vuoto pubblicato
    scenari = _scenari()
    _con_bucket(scenari, GUILD_EDGE, "voice", lambda s: s + [CommunitySizeBucket(
        bucket="100+", quality=Quality(suppressed=False, details={}),
        values=CommunitySizeValues(community_count=0, member_count=0),
    )])
    assert any("bucket vuoto" in p for p in problemi_di_contenuto(scenari))

    # 3. somma dei membri diversa da n (nessun bucket soppresso)
    scenari = _scenari()
    _con_bucket(scenari, GUILD_EDGE, "voice", lambda s: [
        _sostituisci_valori(s[0], member_count=s[0].values.member_count + 1), *s[1:]
    ])
    assert any("somma dei member_count" in p for p in problemi_di_contenuto(scenari))

    # 4. bucket pubblicato sotto soglia
    scenari = _scenari()
    _con_bucket(scenari, GUILD_EDGE, "reaction", lambda s: [
        b.model_copy(update={
            "quality": Quality(suppressed=False, details={}),
            "values": CommunitySizeValues(community_count=1, member_count=3),
        }) if b.bucket == "small" else b
        for b in s
    ])
    assert any("sotto soglia" in p for p in problemi_di_contenuto(scenari))

    # 5a. secondaria senza primaria
    scenari = _scenari()
    _con_bucket(scenari, GUILD_EDGE, "reaction", lambda s: [b for b in s if b.bucket != "small"])
    assert any("secondaria senza una primaria" in p for p in problemi_di_contenuto(scenari))

    # 5b. madre soppressa con un bucket pubblicato
    scenari = _scenari()
    _con_bucket(scenari, GUILD_EDGE, "reply", lambda s: [CommunitySizeBucket(
        bucket="small", quality=Quality(suppressed=False, details={}),
        values=CommunitySizeValues(community_count=1, member_count=3),
    )])
    assert any("madre soppressa con bucket pubblicati" in p for p in problemi_di_contenuto(scenari))


def test_la_secondaria_di_reaction_arriva_dall_algoritmo():
    reaction = next(c for c in SCENARIOS[GUILD_EDGE]["communities"] if c.layer == "reaction")
    motivi = {b.bucket: b.quality.suppression_reason for b in reaction.sizes}
    assert motivi == {"small": "below_threshold", "5-9": "secondary", "10-19": None, "20-49": None}


# --- la forma degli scenari che la vista Robustezza richiede ----------------


def _parti(scenario):
    parti = {}
    for r in scenario["robustness"]:
        parti.setdefault((r.snapshot_id, r.layer), []).append(r)
    return parti


def test_003_copre_i_casi_concordati():
    parti = _parti(SCENARIOS[GUILD_EDGE])
    # Tutti e quattro i layer hanno righe: il layer assente sta in ...002.
    assert {layer for _sid, layer in parti} == {"voice", "reply", "mention", "reaction"}
    assert all(r.quality.significant for r in parti[(77, "reaction")])
    voice = parti[(77, "voice")]
    assert any(r.values.targeted_excess < 0 for r in voice)
    assert all(r.quality.suppressed for r in parti[(77, "reply")])
    mention = {r.removal_fraction: r for r in parti[(77, "mention")]}
    assert mention[0.05].values.nodes_removed == mention[0.10].values.nodes_removed == 1
    assert mention[0.20].values.targeted_z is None


def test_002_voice_e_volatile():
    parti = _parti(SCENARIOS[GUILD_MATURE])
    voice = {sid: righe for (sid, layer), righe in parti.items() if layer == "voice"}
    assert 40 not in voice, "assente nell'ultimo snapshot"
    assert 35 not in voice, "assente in uno snapshot intermedio"
    assert all(r.quality.suppressed for r in voice[33])
    assert any(r.quality.significant for r in voice[31])
    reply = parti[(34, "reply")]
    assert any(r.values.targeted_excess < 0 for r in reply)


def test_002_prima_osservazione_confrontabile_e_significativa_senza_stabilita():
    # Lo stato di modello-metriche.md 4.6 corretto: nessun precedente confrontabile
    # non e' un motivo di non significativita'.
    comm = {c.layer: c for c in SCENARIOS[GUILD_MATURE]["communities"] if c.snapshot_id == 29}
    for layer in ("reply", "mention", "reaction"):
        c = comm[layer]
        assert c.quality.significant is True
        assert c.quality.n_effective >= PARAMS.min_nodes_structural
        assert c.values.modularity_z >= PARAMS.min_modularity_z
        assert c.previous_snapshot_id is None
        assert c.values.previous_gap_days is None and c.values.node_overlap is None
        assert c.values.stability_jaccard is None
        assert c.quality.details["stability_unavailable"] == "params_differ"


def test_002_voice_che_riappare_si_confronta_con_una_partizione_vuota():
    # job/main.py::_previous_partition: il layer vuoto nel precedente da' {}, non
    # None. Non e' "nessun precedente": e' overlap 0 e tutte le community nate.
    c = next(c for c in SCENARIOS[GUILD_MATURE]["communities"] if c.snapshot_id == 36 and c.layer == "voice")
    assert c.previous_snapshot_id == 35 and c.values.previous_gap_days == 7.0
    assert c.values.node_overlap == 0.0 and c.values.stability_jaccard is None
    assert c.values.communities_born == c.values.community_count
    assert c.quality.significant is False


def test_001_rispecchia_la_produzione():
    s = SCENARIOS[GUILD_TODAY]
    assert [r.snapshot_id for r in s["runs"]] == [12, 11]
    assert {r.code_version for r in s["runs"]} == {"9d0dc98"}
    # Intero, non stringa: e' asdict a scriverlo. '2' e' solo come lo mostra psql con ->>.
    assert {r.params["voice_structural_min_sessions"] for r in s["runs"]} == {2}

    # Snapshot 11: lo snapshot 10 non esiste piu', e il rerun ha riscritto TUTTI i
    # layer senza precedente — non solo voice.
    undici = [c for c in s["communities"] if c.snapshot_id == 11]
    assert len(undici) == 4
    for c in undici:
        assert c.previous_snapshot_id is None
        assert c.values.stability_jaccard is None and c.values.previous_gap_days is None
        assert c.quality.details["stability_unavailable"] == "no_previous_snapshot"

    # Snapshot 12: stesso precedente e stesso gap per tutti, stabilita' solo su voice.
    dodici = {c.layer: c for c in s["communities"] if c.snapshot_id == 12}
    assert {(c.previous_snapshot_id, c.values.previous_gap_days) for c in dodici.values()} == {(11, 6.823)}
    assert {l for l, c in dodici.items() if c.values.stability_jaccard is not None} == {"voice"}

    # voice dopo voice_structural_min_sessions = 2: 9 e 15 nodi.
    voice_n = {r.snapshot_id: r.quality.n_effective for r in s["robustness"] if r.layer == "voice"}
    assert voice_n == {11: 9, 12: 15}


# --- il limite conta snapshot, non righe -----------------------------------


@pytest.fixture
def api():
    with TestClient(fixture_api.app) as client:
        yield client


def test_limit_conta_snapshot_anche_quando_un_layer_manca(api):
    # ...002, ultimo snapshot: voice assente, quindi 9 righe e non 12. Affettare
    # per righe (limit * 12) avrebbe portato dentro tre righe dello snapshot 39.
    righe = api.get(f"/guilds/{GUILD_MATURE}/robustness", params={"limit": 1}).json()
    assert {r["snapshot_id"] for r in righe} == {40}
    assert len(righe) == 9

    righe = api.get(f"/guilds/{GUILD_MATURE}/robustness", params={"limit": 3}).json()
    assert {r["snapshot_id"] for r in righe} == {40, 39, 38}

    comm = api.get(f"/guilds/{GUILD_MATURE}/communities", params={"limit": 1}).json()
    assert {c["snapshot_id"] for c in comm} == {40}
    assert len(comm) == 3


def test_ordinamento_come_l_api(api):
    righe = api.get(f"/guilds/{GUILD_TODAY}/robustness").json()
    chiavi = [(r["as_of"], r["layer"], r["removal_fraction"]) for r in righe]
    atteso = sorted(chiavi, key=lambda k: (k[1], k[2]))
    atteso.sort(key=lambda k: k[0], reverse=True)
    assert chiavi == atteso


def test_coorti_affettate_per_snapshot(api):
    # limit=1: le coorti dell'ultimo run, lo snapshot 12 — quello a cui punta
    # latest_metrics_as_of. Una pagina Coorti vuota qui sarebbe la guild che deve
    # rispecchiare la produzione mostrata come se non avesse coorti.
    gruppi = api.get(f"/guilds/{GUILD_TODAY}/cohorts", params={"limit": 1}).json()
    assert {g["snapshot_id"] for g in gruppi} == {12}
    assert [g["cohort_start"] for g in gruppi] == ["2026-09-07", "2026-08-31", "2026-08-17", "2026-08-10"]
    gruppi = api.get(f"/guilds/{GUILD_TODAY}/cohorts", params={"limit": 2}).json()
    assert len(gruppi) == 7


# --- coorti: chiamate al job, righe vere di produzione ----------------------


def _gruppo(gid, start, sid):
    return next(g for g in SCENARIOS[gid]["cohorts"]
                if str(g.cohort_start) == start and g.snapshot_id == sid)


def _onb(gruppo, scope):
    return next(o for o in gruppo.onboarding if o.layer_scope == scope)


# Le righe di produzione di ...001 (query sulla droplet, 14/09/2026), per campo:
# (n, observation_days, is_mature, copertura, survivors, event, censored,
#  censored_by_leave, median, median_reached, p25, p75, reached_14d, reached_28d)
_PRODUZIONE_001 = {
    ("2026-08-17", 11, "any"): (8, 15, True, False, True, 0, 8, 0, None, False, None, None, 0.0, None),
    ("2026-08-17", 12, "any"): (8, 22, True, False, True, 0, 8, 0, None, False, None, None, 0.0, None),
    ("2026-08-31", 11, "any"): (14, 0, False, True, False, 0, 14, 2, None, False, None, None, None, None),
    ("2026-08-31", 12, "any"): (14, 7, False, True, False, 1, 13, 2, None, False, 11.157546418090279, None, None, None),
    ("2026-08-31", 12, "voice"): (14, 7, False, True, False, 0, 14, 2, None, False, None, None, None, None),
    ("2026-09-07", 12, "any"): (8, 1, False, True, False, 1, 7, 1, 4.582487893043981, True, 4.582487893043981, None, None, None),
}


@pytest.mark.parametrize("chiave", sorted(_PRODUZIONE_001))
def test_le_coorti_di_001_riproducono_la_produzione_al_bit(chiave):
    start, sid, scope = chiave
    o = _onb(_gruppo(GUILD_TODAY, start, sid), scope)
    q, v = o.quality, o.values
    reale = (q.n_effective, q.observation_days, q.is_mature, q.has_snapshot_coverage, q.is_survivors_only,
             v.event_count, v.censored_count, v.censored_by_leave, v.median_days_to_k, v.median_reached,
             v.p25_days_to_k, v.p75_days_to_k, v.reached_by_14d, v.reached_by_28d)
    # Uguaglianza esatta, float compresi: sono i valori che il job ha scritto.
    assert reale == _PRODUZIONE_001[chiave]
    assert q.significant is False


def test_001_retention_e_soppressioni_come_in_produzione():
    ret = {t.horizon_days: t for t in _gruppo(GUILD_TODAY, "2026-08-31", 12).retention}
    # Non arrotondato: 12/14 come lo scrive il job.
    assert ret[7].values.retained_fraction == 0.8571428571428571
    assert ret[14].values.not_computable_reason == ret[28].values.not_computable_reason == "horizon_not_reached"
    for sid in (11, 12):
        g = _gruppo(GUILD_TODAY, "2026-08-10", sid)
        for r in [*g.onboarding, *g.retention]:
            assert r.quality.suppressed
        # Assente, non False.
        assert all(t.values.is_computable is None for t in g.retention)
    assert not any(g.cohort_start.isoformat() == "2026-09-07" and g.snapshot_id == 11
                   for g in SCENARIOS[GUILD_TODAY]["cohorts"])


def test_003_coorti_negli_stati_concordati():
    gruppi = {str(g.cohort_start): g for g in SCENARIOS[GUILD_EDGE]["cohorts"]}
    assert all(r.quality.suppressed for r in [*gruppi["2026-08-31"].onboarding, *gruppi["2026-08-31"].retention])
    matura = _onb(gruppi["2026-08-10"], "any")
    assert matura.quality.significant is True and matura.values.median_reached is False
    sopravvissuti = _onb(gruppi["2026-04-13"], "any")
    assert sopravvissuti.quality.is_survivors_only is True
    assert all(t.values.not_computable_reason == "before_observability_anchor" for t in gruppi["2026-04-13"].retention)


def test_coglie_un_run_senza_coorti():
    scenari = _scenari()
    scenari[GUILD_TODAY]["cohorts"] = [g for g in scenari[GUILD_TODAY]["cohorts"] if g.snapshot_id != 12]
    assert any("nessuna coorte" in p for p in problemi_di_contenuto(scenari))


def test_coglie_la_retention_arrotondata_ma_non_12_su_14():
    # Il valore vero passa con la tolleranza...
    assert problemi_di_contenuto(SCENARIOS) == []
    # ...e un valore che non ricompone un conteggio no.
    scenari = _scenari()
    gruppi = scenari[GUILD_TODAY]["cohorts"]
    i = next(i for i, g in enumerate(gruppi) if str(g.cohort_start) == "2026-08-31" and g.snapshot_id == 12)
    ret = list(gruppi[i].retention)
    ret[0] = _sostituisci_valori(ret[0], retained_fraction=0.857)
    gruppi[i] = gruppi[i].model_copy(update={"retention": ret})
    assert any("non e' un conteggio" in p for p in problemi_di_contenuto(scenari))


def _con_onboarding(scenari, gid, start, sid, **campi_quality_o_values):
    gruppi = scenari[gid]["cohorts"]
    i = next(i for i, g in enumerate(gruppi) if str(g.cohort_start) == start and g.snapshot_id == sid)
    onb = []
    for o in gruppi[i].onboarding:
        q = {k: v for k, v in campi_quality_o_values.items() if k in type(o.quality).model_fields}
        v = {k: v for k, v in campi_quality_o_values.items() if k in type(o.values).model_fields}
        onb.append(o.model_copy(update={"quality": o.quality.model_copy(update=q),
                                        "values": o.values.model_copy(update=v)}))
    gruppi[i] = gruppi[i].model_copy(update={"onboarding": onb})


def test_coglie_maturita_ed_eventi_scritti_a_mano():
    scenari = _scenari()
    _con_onboarding(scenari, GUILD_TODAY, "2026-08-31", 12, is_mature=True)
    assert any("is_mature" in p for p in problemi_di_contenuto(scenari))

    scenari = _scenari()
    # L'aritmetica della versione precedente: event_count = n, censored = n - 4.
    _con_onboarding(scenari, GUILD_TODAY, "2026-08-17", 12, event_count=8, censored_count=4)
    assert any("event_count + censored_count" in p for p in problemi_di_contenuto(scenari))


def test_coglie_una_coorte_oltre_i_180_giorni():
    scenari = _scenari()
    gruppi = scenari[GUILD_EDGE]["cohorts"]
    gruppi[0] = gruppi[0].model_copy(update={"cohort_start": date(2026, 2, 9)})
    assert any("cohort_max_age_days" in p for p in problemi_di_contenuto(scenari))
