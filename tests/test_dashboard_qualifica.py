"""Il motore di rendering della qualificazione: cinque stati, cinque rendering.

Le righe sono istanze dei modelli veri di ``api/models.py``, non dizionari scritti
a mano: un campo rinominato lato API rompe questi test subito, invece di lasciarli
passare contro una forma che l'API non produce piu'.

Il difetto che questi test esistono per prendere non fa mai fallire niente in
produzione: una riga soppressa mostrata come valore, un ``None`` di
significativita' mostrato come ``False``, due etichette collassate in una. Ogni
volta la pagina si rende, e dice una cosa sbagliata con l'aria di dirne una
giusta (CLAUDE.md 7).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from api.models import (
    CohortQuality,
    CommunitySizeBucket,
    CommunitySizeValues,
    OnboardingQuality,
    OnboardingRow,
    OnboardingValues,
    Quality,
    RetentionRow,
    RetentionValues,
    RobustnessRow,
    RobustnessValues,
)
from dashboard import qualifica
from dashboard.main import TEMPLATES_DIR
from dashboard.qualifica import (
    ASSENTE,
    MEDIANA_NON_RAGGIUNTA,
    NON_CALCOLABILE,
    NON_SIGNIFICATIVO,
    NON_VALUTATO,
    SOLO_SOPRAVVISSUTI,
    SOPPRESSO,
    VALORE,
    Cella,
    cella,
)

AS_OF = datetime(2026, 9, 7, tzinfo=timezone.utc)


# --- costruttori di righe, sui modelli veri ---------------------------------


def robustezza(*, suppressed=False, significant=False, excess=0.31, details=None, **valori):
    if suppressed:
        return RobustnessRow(
            snapshot_id=1, as_of=AS_OF, layer="voice", removal_fraction=0.05,
            quality=Quality(suppressed=True, suppression_reason="below_threshold",
                            significant=None, details={}),
            values=RobustnessValues(),
        )
    return RobustnessRow(
        snapshot_id=1, as_of=AS_OF, layer="voice", removal_fraction=0.10,
        quality=Quality(n_effective=40, suppressed=False, significant=significant,
                        details=details if details is not None else {}),
        values=RobustnessValues(nodes_removed=4, giant_before=1.0,
                                targeted_excess=excess, **valori),
    )


def onboarding(*, significant=False, survivors=False, median=9.5, median_reached=True):
    return OnboardingRow(
        layer_scope="any", k=5,
        quality=OnboardingQuality(n_effective=9, suppressed=False, significant=significant,
                                  is_survivors_only=survivors, excluded_rejoins=0,
                                  is_mature=True, has_snapshot_coverage=True,
                                  observation_days=40),
        values=OnboardingValues(event_count=5, censored_count=4, median_days_to_k=median,
                                median_reached=median_reached, p25_days_to_k=4.0,
                                p75_days_to_k=17.0, reached_by_14d=0.38, reached_by_28d=0.61),
    )


def retention(*, suppressed=False, computable=True, fraction=0.73, reason=None, survivors=False):
    if suppressed:
        return RetentionRow(
            horizon_days=28,
            quality=CohortQuality(suppressed=True, suppression_reason="below_threshold",
                                  significant=None, details={}),
            values=RetentionValues(),
        )
    return RetentionRow(
        horizon_days=28,
        quality=CohortQuality(n_effective=11, suppressed=False, significant=None,
                              is_survivors_only=survivors, excluded_rejoins=0),
        values=RetentionValues(retained_fraction=fraction, is_computable=computable,
                               not_computable_reason=reason),
    )


# --- i cinque stati ---------------------------------------------------------


def test_soppresso_e_terminale_e_non_porta_nessun_altra_etichetta():
    c = cella(robustezza(suppressed=True), "targeted_excess")

    assert c.esito == SOPPRESSO
    assert c.motivo == "below_threshold"
    assert c.spiegazione == "troppo poche persone per mostrare il dato"
    # significant e' None su una riga soppressa, ma "non valutata" NON si mostra:
    # la soppressione chiude la questione (dashboard.md 5, composizione).
    assert c.etichette == ()
    assert c.stati == {SOPPRESSO}
    # Mai uno zero, mai una cella vuota.
    assert c.testo and c.testo != "0"


def test_non_significativo_mostra_il_numero_con_l_etichetta():
    c = cella(robustezza(significant=False, excess=0.31), "targeted_excess")

    assert c.esito == VALORE
    assert c.testo == "0,310"  # il numero si mostra: nasconderlo toglierebbe la serie
    assert c.stati == {VALORE, NON_SIGNIFICATIVO}


def test_non_valutato_e_uno_stato_diverso_da_non_significativo():
    # quality.significant ha TRE stati, e None non collassa in False: sulla
    # retention (nessuna colonna is_significant, migrations/0006_metrics.sql) un
    # `not significant` metterebbe "non significativo" su ogni numero.
    # Ma None non ha nemmeno un rendering proprio (dashboard.md 5): nessuna
    # etichetta, ne' "non significativo" ne' "non valutata".
    c = cella(retention(), "retained_fraction")

    assert c.esito == VALORE
    assert c.testo == "0,730"
    assert c.stati == {VALORE}
    assert c.etichette == ()

    stesso_numero_ma_false = robustezza(significant=False)
    assert NON_SIGNIFICATIVO in cella(stesso_numero_ma_false, "targeted_excess").stati


def test_significativo_non_porta_etichette():
    c = cella(robustezza(significant=True, excess=0.44), "targeted_excess")

    assert c.stati == {VALORE}
    assert c.etichette == ()


def test_non_calcolabile_e_una_parola_diversa_da_soppresso():
    c = cella(retention(computable=False, fraction=None, reason="horizon_not_reached"),
              "retained_fraction")

    assert c.esito == NON_CALCOLABILE
    assert c.motivo == "horizon_not_reached"
    assert c.spiegazione == "l'orizzonte non e' ancora trascorso per tutta la coorte"
    assert SOPPRESSO not in c.stati
    assert c.testo == "non calcolabile"


def test_soppressione_vince_su_non_calcolabile():
    # Ordine delle domande: prima "il numero esiste?" a livello di riga.
    c = cella(retention(suppressed=True), "retained_fraction")

    assert c.stati == {SOPPRESSO}


def test_solo_sopravvissuti_e_non_significativo_si_mostrano_insieme():
    # La forma NORMALE di una coorte anteriore all'ancora (job/cohorts.py:
    # is_significant = not reasons, e survivors_only aggiunge sempre una reason).
    # Collassarle in un solo "non significativo" perderebbe l'informazione che
    # conta di piu'.
    c = cella(onboarding(significant=False, survivors=True), "event_count")

    assert c.esito == VALORE
    assert c.testo == "5"
    assert c.stati == {VALORE, NON_SIGNIFICATIVO, SOLO_SOPRAVVISSUTI}
    tipi = [e.tipo for e in c.etichette]
    assert tipi == [NON_SIGNIFICATIVO, SOLO_SOPRAVVISSUTI]


def test_solo_sopravvissuti_esiste_solo_nelle_coorti():
    # Il flag non e' universale: una riga di robustezza non ce l'ha, e non deve
    # comparire nessun badge di popolazione.
    assert SOLO_SOPRAVVISSUTI not in cella(robustezza(), "targeted_excess").stati


def test_cinque_stati_quattro_rendering_distinti():
    # dashboard.md 5: "non valutato" e' uno stato senza rendering, quindi i
    # rendering distinti sono quattro. Il quinto stato e' coperto sotto, da
    # test_nessuna_cella_del_fixture_porta_non_valutato.
    casi = {
        SOPPRESSO: cella(robustezza(suppressed=True), "targeted_excess"),
        NON_SIGNIFICATIVO: cella(robustezza(significant=False), "targeted_excess"),
        NON_CALCOLABILE: cella(retention(computable=False, fraction=None,
                                         reason="before_observability_anchor"),
                               "retained_fraction"),
        SOLO_SOPRAVVISSUTI: cella(onboarding(significant=False, survivors=True), "event_count"),
    }
    for stato, c in casi.items():
        assert stato in c.stati, stato

    html = {stato: _rendi(c) for stato, c in casi.items()}
    # Quattro HTML diversi, e ciascuno con il marcatore del proprio stato.
    assert len(set(html.values())) == 4
    assert 'cella--soppresso' in html[SOPPRESSO]
    assert 'etichetta--non_significativo' in html[NON_SIGNIFICATIVO]
    assert 'cella--non_calcolabile' in html[NON_CALCOLABILE]
    assert 'soppresso' not in html[NON_CALCOLABILE]
    assert 'etichetta--solo_sopravvissuti' in html[SOLO_SOPRAVVISSUTI]
    assert 'etichetta--non_significativo' in html[SOLO_SOPRAVVISSUTI]


# --- il sesto stato: median_reached -----------------------------------------


def test_mediana_non_raggiunta_e_una_frase_non_un_assenza():
    riga = onboarding(significant=True, median=None, median_reached=False)
    c = cella(riga, "median_days_to_k")

    assert c.esito == MEDIANA_NON_RAGGIUNTA
    assert c.testo == "meno di metà della coorte ha raggiunto 5 connessioni"
    assert SOPPRESSO not in c.stati and ASSENTE not in c.stati
    # Il flag e' puntuale: gli altri valori della riga restano leggibili.
    assert cella(riga, "p25_days_to_k").stati == {VALORE}
    assert cella(riga, "reached_by_28d").testo == "0,610"


# --- difese contro gli accessi sbagliati ------------------------------------


def test_un_nome_dalla_prosa_fallisce_invece_di_restituire_un_default():
    with pytest.raises(KeyError):
        cella(robustezza(), "is_suppressed")
    with pytest.raises(KeyError):
        # campo di un'altra metrica
        cella(retention(), "targeted_excess")


def test_i_qualificatori_puntuali_non_sono_valori_da_rendere():
    with pytest.raises(ValueError):
        cella(retention(), "is_computable")
    with pytest.raises(ValueError):
        cella(onboarding(), "median_reached")


def test_la_firma_vuole_la_riga_non_il_valore():
    with pytest.raises(TypeError):
        cella(0.31, "targeted_excess")  # type: ignore[arg-type]


def test_details_non_decide_niente():
    # dashboard.md 5, regola 2: details e' diagnostica. Due righe identiche nei
    # campi tipizzati producono la stessa cella, qualunque cosa ci sia dentro.
    con = cella(robustezza(details={"not_significant_because": ["too_few_nodes"]}), "targeted_excess")
    senza = cella(robustezza(details={}), "targeted_excess")
    assert con == senza


def test_none_su_riga_pubblicata_non_somiglia_alla_soppressione():
    # targeted_z None con baseline degenere: non e' soppresso, non e' zero.
    c = cella(robustezza(significant=False, targeted_z=None), "targeted_z")
    assert c.esito == ASSENTE
    assert SOPPRESSO not in c.stati
    assert c.testo == "non disponibile"


def test_targeted_excess_negativo_conserva_il_segno():
    # Senza colonna il valore e' una colonna di un solo valore: tre decimali.
    assert cella(robustezza(excess=-0.07), "targeted_excess").testo == "-0,070"


# --- precisione di colonna (dashboard.md 5) ---------------------------------


@pytest.mark.parametrize("valore,atteso", [
    (-0.0004, "-0,0004"),
    (-0.00001, "-0,00001"),
    (-0.0, "0,0"),
    (0.0, "0,0"),
])
def test_formatta_non_produce_mai_uno_zero_con_segno(valore, atteso):
    testo = qualifica.formatta(valore)
    assert testo == atteso
    assert not (testo.startswith("-") and float(testo.replace(",", ".")) == 0)


def test_con_una_precisione_troppo_bassa_il_segno_sparisce_non_la_regola():
    # La colonna non lo permette mai (la regola 1 sale finche' serve), ma se un
    # chiamante passasse meno decimali del necessario il risultato resta senza
    # segno: -0,000 non deve esistere in nessun caso.
    assert qualifica.formatta(-0.0004, 3) == "0,000"
    assert qualifica.formatta(-0.0, 4) == "0,0000"


def test_colonna_con_zero_esatto_e_valore_minuscolo():
    colonna = [0.0, -0.0004, -0.0]
    d = qualifica.precisione_colonna(colonna)
    assert d == 4
    assert [qualifica.formatta(v, d) for v in colonna] == ["0,0000", "-0,0004", "0,0000"]


def test_colonna_il_minimo_e_un_pavimento_non_toglie_cifre():
    colonna = [1.0, 0.92, 0.8]
    d = qualifica.precisione_colonna(colonna)
    assert d == 3
    assert [qualifica.formatta(v, d) for v in colonna] == ["1,000", "0,920", "0,800"]


def test_colonna_di_soli_zeri():
    d = qualifica.precisione_colonna([0.0, -0.0, 0.0])
    assert d == 1
    assert qualifica.formatta(-0.0, d) == "0,0"


def test_la_precisione_sale_fino_al_valore_non_nullo_piu_piccolo():
    assert qualifica.precisione_colonna([0.31, -0.00001]) == 5
    # Interi e None non entrano nella scelta.
    assert qualifica.precisione_colonna([3, None, 0.5]) == 3


def test_motivo_sconosciuto_si_mostra_letterale():
    riga = CommunitySizeBucket(
        bucket="20-49",
        quality=Quality(suppressed=True, suppression_reason="codice_futuro", details={}),
        values=CommunitySizeValues(),
    )
    assert cella(riga, "member_count").spiegazione == "codice_futuro"


# --- tutte le righe del fixture si rendono ----------------------------------


def test_ogni_cella_di_ogni_scenario_del_fixture_si_rende():
    from tools.fixture_api import SCENARIOS

    viste = 0
    for scenario in SCENARIOS.values():
        righe = list(scenario["robustness"])
        for c in scenario["communities"]:
            righe.append(c)
            righe.extend(c.sizes)
        for gruppo in scenario["cohorts"]:
            righe.extend(gruppo.onboarding)
            righe.extend(gruppo.retention)

        for riga in righe:
            for campo in type(riga.values).model_fields:
                if campo in qualifica._QUALIFICATORI_PUNTUALI:
                    continue
                c = cella(riga, campo)
                assert isinstance(c, Cella)
                assert c.testo, (riga, campo)
                if riga.quality.suppressed:
                    assert c.stati == {SOPPRESSO}
                _rendi(c)
                viste += 1
    assert viste > 1000


# --- "non valutato": un'assenza verificata, non presunta ---------------------


def _righe_del_fixture():
    from tools.fixture_api import SCENARIOS

    for scenario in SCENARIOS.values():
        yield from scenario["robustness"]
        for c in scenario["communities"]:
            yield c
            yield from c.sizes
        for gruppo in scenario["cohorts"]:
            yield from gruppo.onboarding
            yield from gruppo.retention


def test_nessuna_cella_del_fixture_porta_non_valutato():
    # dashboard.md 5, "Perche' 'non valutato' non ha rendering". Da solo questo
    # test non puo' fallire per un cambio nei DATI — cella() non emette mai
    # NON_VALUTATO, qualunque riga riceva — ma fallisce se qualcuno reintroduce
    # l'etichetta nel codice. Il controllo sui dati e' il test successivo.
    portatrici = [
        (type(riga).__name__, campo)
        for riga in _righe_del_fixture()
        for campo in type(riga.values).model_fields
        if campo not in qualifica._QUALIFICATORI_PUNTUALI
        and NON_VALUTATO in cella(riga, campo).stati
    ]
    assert portatrici == []
    assert all(NON_VALUTATO not in _rendi(cella(r, _primo_campo(r)))
               for r in _righe_del_fixture())


# Le righe su cui `significant is None` e' previsto, e perche' nessuna etichetta
# e' giusta li'. Un None altrove e' una situazione che la spec non ha deciso.
#
# - riga soppressa: la soppressione e' terminale;
# - RetentionRow: metric_cohort_retention non ha la colonna is_significant;
# - CommunitySizeBucket: metric_community_sizes non ha la colonna is_significant
#   (migrations/0006_metrics.sql). NON e' tra i "due posti soli" di dashboard.md 5,
#   che su questo punto e' da correggere: stessa ragione della retention.
_NONE_PREVISTO_SU = (RetentionRow, CommunitySizeBucket)


def test_significant_none_compare_solo_dove_la_spec_lo_prevede():
    # Il test che puo' accendersi davvero: se domani una tabella di metriche con
    # is_significant nullable arriva al fixture per una ragione diversa, qui
    # fallisce e costringe a decidere, invece di lasciar passare in silenzio un
    # None che la dashboard renderebbe senza nessuna etichetta.
    imprevisti = sorted({
        type(riga).__name__
        for riga in _righe_del_fixture()
        if riga.quality.significant is None
        and not riga.quality.suppressed
        and not isinstance(riga, _NONE_PREVISTO_SU)
    })
    assert imprevisti == [], (
        "significant is None su righe pubblicate non previste da dashboard.md 5: "
        f"{imprevisti}"
    )


def _primo_campo(riga) -> str:
    return next(c for c in type(riga.values).model_fields
                if c not in qualifica._QUALIFICATORI_PUNTUALI)


# --- aiuto ------------------------------------------------------------------


def _rendi(c: Cella) -> str:
    """La cella come la rende una riga di tabella: valore piu' colonna di qualificazione.

    I due macro sono separati (dashboard.md 5, "Dove va l'etichetta di riga"):
    ``mostra`` non rende piu' le etichette, ``etichette`` si. Rendere solo
    ``mostra`` qui farebbe sparire le etichette da questi test senza che nessuno
    se ne accorga.
    """
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                      autoescape=select_autoescape(["html"]))
    return env.from_string(
        '{% from "_cella.html" import mostra, etichette %}{{ mostra(c) }}{{ etichette(c) }}'
    ).render(c=c)


def test_mostra_non_rende_le_etichette_e_etichette_si():
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                      autoescape=select_autoescape(["html"]))
    c = cella(onboarding(significant=False, survivors=True), "event_count")
    valore = env.from_string('{% from "_cella.html" import mostra %}{{ mostra(c) }}').render(c=c)
    colonna = env.from_string('{% from "_cella.html" import etichette %}{{ etichette(c) }}').render(c=c)

    assert "etichetta--" not in valore
    assert "cella--dequalificata" in valore  # la dequalificazione resta sul valore
    assert "etichetta--non_significativo" in colonna
    assert "etichetta--solo_sopravvissuti" in colonna
