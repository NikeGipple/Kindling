"""I codici che il sistema emette devono avere tutti una traduzione.

**Il buco che questo file chiude.** I codici di motivo vivono in tre posti:
il sorgente del job che li emette, il fixture che li riproduce, e le tabelle di
traduzione di ``dashboard/qualifica.py``. ``tools/fixture_api.py --check``
allinea i primi due: un codice inventato nel fixture, o rinominato nel job, fa
fallire il check. Il terzo posto non lo controllava nessuno.

Non e' teoria: e' gia' successo. Il fixture usava ``below_min_cardinality``,
``secondary_suppression`` e ``horizon_not_elapsed`` mentre il job emette
``below_threshold``, ``secondary`` e ``horizon_not_reached``. La dashboard
mostrava quei codici grezzi al posto delle frasi italiane, e **la suite era
verde lo stesso** — 207 test passati prima della correzione e 207 dopo.

La ragione per cui nessun test se ne accorgeva e' che ``_traduci`` fa
``tabella.get(codice, codice)``: un codice sconosciuto si mostra letterale. E'
una scelta giusta e deliberata — ``test_motivo_sconosciuto_si_mostra_letterale``
la fissa — perche' mostrare un codice e' piu' onesto che nascondere il motivo.
Ma e' una rete di sicurezza, e una rete che si tende senza che nessuno lo noti
e' la classe di difetto di CLAUDE.md 7: il meccanismo funziona, e non dice che
sta funzionando al posto di qualcos'altro.

Questi test verificano che **la rete non serva**: che per ogni codice realmente
prodotto la traduzione esista, e che quindi il fallback non scatti mai in
condizioni normali. Se il job rinomina un codice, o se qualcuno aggiunge un caso
senza tradurlo, qui si rompe qualcosa — invece che in produzione, davanti al
community manager.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from dashboard.qualifica import (
    MOTIVI_NON_CALCOLABILE,
    MOTIVI_SOPPRESSIONE,
    NON_CALCOLABILE,
    SOPPRESSO,
    cella,
)
from tools.fixture_api import SCENARIOS


def _tutte_le_righe() -> Iterator[tuple[str, Any]]:
    """Ogni riga di metrica di ogni scenario, con un'etichetta per i messaggi."""
    for gid, scenario in SCENARIOS.items():
        for row in scenario["robustness"]:
            yield f"{gid}/robustness", row
        for community in scenario["communities"]:
            yield f"{gid}/communities", community
            for bucket in community.sizes:
                yield f"{gid}/communities.sizes", bucket
        for gruppo in scenario["cohorts"]:
            for row in gruppo.onboarding:
                yield f"{gid}/cohorts.onboarding", row
            for row in gruppo.retention:
                yield f"{gid}/cohorts.retention", row


def _un_campo_di(row: Any) -> str:
    """Il primo campo mostrabile della riga: serve solo a far produrre una cella."""
    from dashboard.qualifica import _QUALIFICATORI_PUNTUALI

    for nome in type(row.values).model_fields:
        if nome not in _QUALIFICATORI_PUNTUALI:
            return nome
    raise AssertionError(f"{type(row.values).__name__} non ha campi mostrabili")


def test_ogni_motivo_di_soppressione_prodotto_ha_una_traduzione():
    """Nessun codice di soppressione deve cadere nel fallback di _traduci."""
    senza_traduzione = {
        (etichetta, row.quality.suppression_reason)
        for etichetta, row in _tutte_le_righe()
        if row.quality.suppressed
        and row.quality.suppression_reason is not None
        and row.quality.suppression_reason not in MOTIVI_SOPPRESSIONE
    }
    assert not senza_traduzione, (
        "codici di soppressione senza traduzione in MOTIVI_SOPPRESSIONE: "
        f"{sorted(senza_traduzione)}"
    )


def test_ogni_motivo_di_non_calcolabilita_prodotto_ha_una_traduzione():
    """Come sopra per la retention, l'unica che ha not_computable_reason."""
    senza_traduzione = {
        (etichetta, row.values.not_computable_reason)
        for etichetta, row in _tutte_le_righe()
        if getattr(row.values, "not_computable_reason", None) is not None
        and row.values.not_computable_reason not in MOTIVI_NON_CALCOLABILE
    }
    assert not senza_traduzione, (
        "codici senza traduzione in MOTIVI_NON_CALCOLABILE: "
        f"{sorted(senza_traduzione)}"
    )


def test_la_spiegazione_resa_non_e_mai_il_codice_grezzo():
    """Il controllo dal lato dell'output, non delle tabelle.

    I due test sopra guardano le chiavi; questo guarda cosa esce davvero da
    ``cella()``. Sono due modi di sbagliare diversi: una tabella puo' avere la
    chiave giusta e il rendering prendere un'altra strada.
    """
    grezzi = []
    for etichetta, row in _tutte_le_righe():
        c = cella(row, _un_campo_di(row))
        if c.esito not in (SOPPRESSO, NON_CALCOLABILE):
            continue
        if c.motivo is not None and c.spiegazione == c.motivo:
            grezzi.append((etichetta, c.esito, c.motivo))
    assert not grezzi, (
        "la dashboard mostrerebbe il codice grezzo invece della spiegazione: "
        f"{sorted(grezzi)}"
    )


@pytest.mark.parametrize(
    "tabella,nome",
    [(MOTIVI_SOPPRESSIONE, "MOTIVI_SOPPRESSIONE"),
     (MOTIVI_NON_CALCOLABILE, "MOTIVI_NON_CALCOLABILE")],
)
def test_nessuna_traduzione_coincide_col_codice(tabella: dict[str, str], nome: str):
    """Una traduzione uguale al codice renderebbe i test sopra ciechi.

    ``{"below_threshold": "below_threshold"}`` soddisferebbe ogni controllo
    sulle chiavi e mostrerebbe comunque il codice grezzo. E' il modo in cui un
    test si aggira senza accorgersene.
    """
    identiche = [k for k, v in tabella.items() if k == v]
    assert not identiche, f"{nome}: traduzioni identiche al codice: {identiche}"
