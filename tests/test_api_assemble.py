"""La forma delle risposte: i flag non si perdono e non cambiano livello.

Fixture sintetiche e nessun database, come per il resto del progetto: qui si
prova la traduzione da riga a risposta, che e' la parte che puo' sbagliare in
silenzio — un flag messo nel posto sbagliato non fa fallire niente, produce una
risposta plausibile in cui un numero inaffidabile sembra buono.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from api import assemble
from api.models import MetricRow

T0 = datetime(2026, 9, 1, 4, 15, tzinfo=timezone.utc)


def robustness_row(**overrides):
    row = {
        "snapshot_id": 42,
        "as_of": T0,
        "layer": "voice",
        "removal_fraction": 0.10,
        "n_effective": 9,
        "nodes_removed": 1,
        "giant_before": 1.0,
        "giant_after_targeted": 0.111,
        "components_after_targeted": 8,
        "giant_after_random_mean": 0.9,
        "giant_after_random_sd": 0.12,
        "components_after_random_mean": 1.1,
        "targeted_excess": 0.789,
        "targeted_z": 6.5,
        "is_suppressed": False,
        "suppression_reason": None,
        "is_significant": False,
        "details": json.dumps({"not_significant_because": ["too_few_nodes"]}),
    }
    row.update(overrides)
    return row


def test_i_valori_stanno_dentro_values_e_la_qualita_accanto():
    riga = assemble.robustness(robustness_row())

    # Per arrivare al numero bisogna passare da values: non c'e' un
    # targeted_excess al primo livello che si possa leggere senza vedere
    # quality come suo fratello.
    assert riga.values.targeted_excess == 0.789
    assert not hasattr(riga, "targeted_excess")
    assert riga.quality.significant is False
    assert riga.quality.details["not_significant_because"] == ["too_few_nodes"]


def test_riga_pubblicata_ma_non_significativa_conserva_i_valori():
    # Il caso reale di oggi: nove nodi. La riga esiste, i numeri ci sono, e
    # dichiara di non essere affidabile. E' uno stato distinto sia da
    # "soppressa" sia da "buona".
    riga = assemble.robustness(robustness_row())

    assert riga.quality.suppressed is False
    assert riga.quality.significant is False
    assert riga.quality.n_effective == 9
    assert riga.values.giant_before == 1.0


def test_riga_soppressa_ha_values_presente_con_tutti_i_campi_nulli():
    # Se values sparisse, "soppressa" somiglierebbe ad "assente" a livello di
    # JSON — la stessa confusione tra zero e NULL che il layer di calcolo evita
    # in tabella.
    riga = assemble.robustness(
        robustness_row(
            n_effective=None,
            nodes_removed=None,
            giant_before=None,
            giant_after_targeted=None,
            components_after_targeted=None,
            giant_after_random_mean=None,
            giant_after_random_sd=None,
            components_after_random_mean=None,
            targeted_excess=None,
            targeted_z=None,
            is_suppressed=True,
            suppression_reason="below_threshold",
            is_significant=None,
            details="{}",
        )
    )

    assert riga.quality.suppressed is True
    assert riga.quality.suppression_reason == "below_threshold"
    # NULL e non False: su una riga soppressa la metrica non e' stata valutata.
    assert riga.quality.significant is None
    assert riga.values is not None
    assert riga.values.targeted_excess is None
    assert riga.values.nodes_removed is None
    # E la chiave sopravvive: e' cio' che rende la riga una riga.
    assert (riga.layer, riga.removal_fraction) == ("voice", 0.10)


def test_quality_e_obbligatorio_nel_modello_base():
    # Una risposta nuova che se ne dimentica non compila lo schema, invece di
    # partire e perdere i flag in produzione.
    #
    # L'accesso e' scritto per reggere sia pydantic v1 (`__fields__`, `.required`)
    # sia v2 (`model_fields`, `.is_required()`): i modelli girano su entrambe, e
    # un test che gira solo sulla versione installata qui non proverebbe niente
    # sulla versione che gira sulla droplet.
    campi = getattr(MetricRow, "model_fields", None) or MetricRow.__fields__
    campo = campi["quality"]
    richiesto = campo.is_required() if hasattr(campo, "is_required") else campo.required
    assert richiesto is True


def test_details_malformato_non_fa_cadere_la_riga():
    # details e' diagnostica, non contratto: una diagnostica illeggibile non
    # deve impedire di leggere la metrica a cui e' attaccata.
    riga = assemble.robustness(robustness_row(details="non-json"))
    assert riga.quality.details == {}
    assert riga.values.targeted_excess == 0.789


# --- community -------------------------------------------------------------


def test_i_bucket_finiscono_sotto_la_loro_partizione_e_non_su_un_altra():
    rows = [
        {
            "snapshot_id": 42,
            "as_of": T0,
            "layer": "voice",
            "n_effective": 40,
            "community_count": 2,
            "modularity": 0.49,
            "is_suppressed": False,
            "is_significant": True,
            "details": "{}",
        },
        {
            "snapshot_id": 42,
            "as_of": T0,
            "layer": "reply",
            "n_effective": 5,
            "community_count": 1,
            "is_suppressed": False,
            "is_significant": False,
            "details": "{}",
        },
    ]
    sizes = [
        {
            "snapshot_id": 42,
            "layer": "voice",
            "bucket": "20-49",
            "community_count": 2,
            "member_count": 40,
            "is_suppressed": False,
            "suppression_reason": None,
        },
        {
            "snapshot_id": 42,
            "layer": "reply",
            "bucket": "small",
            "community_count": 1,
            "member_count": None,
            "is_suppressed": True,
            "suppression_reason": "below_threshold",
        },
    ]

    voice, reply = assemble.communities(rows, sizes)

    assert [b.bucket for b in voice.sizes] == ["20-49"]
    assert [b.bucket for b in reply.sizes] == ["small"]
    # Ogni bucket porta il proprio quality: la soppressione qui e' per bucket.
    assert reply.sizes[0].quality.suppressed is True
    assert reply.sizes[0].values.member_count is None
    assert voice.sizes[0].quality.suppressed is False


# --- coorti ----------------------------------------------------------------


def cohort_row(**overrides):
    row = {
        "snapshot_id": 42,
        "as_of": T0,
        "cohort_start": date(2026, 3, 9),
        "layer_scope": "any",
        "k": 5,
        "n_effective": 40,
        "observation_days": 170,
        "is_mature": True,
        "event_count": 0,
        "censored_count": 40,
        "censored_by_leave": 0,
        "median_days_to_k": None,
        "median_reached": False,
        "p25_days_to_k": None,
        "p75_days_to_k": None,
        "reached_by_14d": None,
        "reached_by_28d": None,
        "excluded_rejoins": 0,
        "is_survivors_only": True,
        "has_snapshot_coverage": False,
        "is_suppressed": False,
        "suppression_reason": None,
        "is_significant": False,
        "details": "{}",
    }
    row.update(overrides)
    return row


def retention_row(**overrides):
    row = {
        "snapshot_id": 42,
        "as_of": T0,
        "cohort_start": date(2026, 3, 9),
        "horizon_days": 28,
        "n_effective": 40,
        "excluded_rejoins": 0,
        "is_survivors_only": True,
        "retained_fraction": None,
        "is_computable": False,
        "not_computable_reason": "before_observability_anchor",
        "is_suppressed": False,
        "suppression_reason": None,
    }
    row.update(overrides)
    return row


def test_i_flag_puntuali_stanno_accanto_al_valore_che_qualificano():
    # median_reached qualifica SOLO median_days_to_k, e is_computable SOLO
    # retained_fraction: in quality sembrerebbero giudizi sull'intera riga.
    gruppo = assemble.cohorts([cohort_row()], [retention_row()])[0]

    onboarding = gruppo.onboarding[0]
    assert onboarding.values.median_reached is False
    assert not hasattr(onboarding.quality, "median_reached")

    retention = gruppo.retention[0]
    assert retention.values.is_computable is False
    assert retention.values.not_computable_reason == "before_observability_anchor"
    assert not hasattr(retention.quality, "is_computable")


def test_onboarding_e_retention_portano_ognuno_il_proprio_quality():
    # Il raggruppamento per cohort_start non deve far collassare due
    # qualificazioni diverse in una sola: qui l'onboarding e' pubblicato e non
    # significativo, la retention e' soppressa.
    gruppo = assemble.cohorts(
        [cohort_row()],
        [
            retention_row(
                n_effective=None,
                excluded_rejoins=None,
                is_survivors_only=None,
                retained_fraction=None,
                is_computable=None,
                not_computable_reason=None,
                is_suppressed=True,
                suppression_reason="below_threshold",
            )
        ],
    )[0]

    assert gruppo.onboarding[0].quality.suppressed is False
    assert gruppo.onboarding[0].quality.significant is False
    assert gruppo.retention[0].quality.suppressed is True
    assert gruppo.retention[0].quality.significant is None


def test_is_survivors_only_arriva_su_entrambe_le_righe():
    # E' la ragione per cui il denominatore e' duplicato in tabella: una
    # divergenza deve restare visibile anche nella risposta.
    gruppo = assemble.cohorts([cohort_row()], [retention_row()])[0]

    assert gruppo.onboarding[0].quality.is_survivors_only is True
    assert gruppo.retention[0].quality.is_survivors_only is True
    assert gruppo.onboarding[0].quality.n_effective == gruppo.retention[0].quality.n_effective


def test_coorti_di_snapshot_diversi_non_si_mescolano():
    gruppi = assemble.cohorts(
        [cohort_row(snapshot_id=42), cohort_row(snapshot_id=41)],
        [retention_row(snapshot_id=42), retention_row(snapshot_id=41)],
    )

    assert [g.snapshot_id for g in gruppi] == [42, 41]
    for gruppo in gruppi:
        assert len(gruppo.onboarding) == 1
        assert len(gruppo.retention) == 1


def test_retention_senza_onboarding_conserva_il_proprio_as_of():
    # Puo' succedere: la retention non dipende da layer_scope ne' da k, quindi
    # una coorte puo' avere righe di retention e nessuna riga di onboarding.
    gruppo = assemble.cohorts([], [retention_row()])[0]

    assert gruppo.as_of == T0
    assert gruppo.onboarding == []
    assert len(gruppo.retention) == 1


# --- guild -----------------------------------------------------------------


def test_lancora_e_i_buchi_di_osservazione_arrivano_nella_risposta():
    riga = assemble.guild(
        {
            "guild_id": 111,
            "first_seen_at": T0,
            "backfilled_at": T0,
            "left_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "rejoined_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
            "latest_metrics_as_of": T0,
        }
    )

    # first_seen_at e' la SPIEGAZIONE di is_survivors_only: servire i caveat
    # senza la ragione dei caveat e' peggio che non servirli.
    assert riga.first_seen_at == T0
    # Entrambi valorizzati = l'ancora di questa guild non e' piu' un istante
    # solo, e chi legge le metriche di coorte deve poterlo sapere.
    assert riga.left_at is not None and riga.rejoined_at is not None


def test_guild_senza_metriche_resta_nellelenco():
    riga = assemble.guild(
        {"guild_id": 222, "first_seen_at": T0, "latest_metrics_as_of": None}
    )
    assert riga.latest_metrics_as_of is None


# --- lo schema pubblicato ---------------------------------------------------


def test_ogni_riga_di_metrica_pubblica_quality_nello_schema_openapi():
    """La difesa deve reggere anche a valle, in cio' che i consumatori leggono.

    Non basta che i modelli Python abbiano ``quality``: se un endpoint nuovo
    restituisse un modello che non eredita da ``MetricRow``, lo schema pubblicato
    lo direbbe — e nessuno lo guarderebbe. Qui lo si guarda.
    """
    from api.main import app

    schema = app.openapi()
    definizioni = schema.get("components", {}).get("schemas", {})

    percorsi_di_metrica = [
        "/guilds/{guild_id}/robustness",
        "/guilds/{guild_id}/communities",
    ]
    for percorso in percorsi_di_metrica:
        risposta = schema["paths"][percorso]["get"]["responses"]["200"]
        contenuto = risposta["content"]["application/json"]["schema"]
        nome = contenuto["items"]["$ref"].rsplit("/", 1)[-1]
        proprieta = definizioni[nome]["properties"]
        assert "quality" in proprieta, f"{nome} non pubblica quality"
        assert "values" in proprieta, f"{nome} non pubblica values"
        # quality e' richiesto, non opzionale.
        assert "quality" in definizioni[nome]["required"], nome
