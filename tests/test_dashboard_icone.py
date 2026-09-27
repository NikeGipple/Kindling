"""Le icone dei server, dalla risposta di Discord alla sessione (dashboard.md 4).

L'hash arriva con i nomi, da ``GET /users/@me/guilds``, e segue le loro regole:
non vota, si valida in scrittura, in sessione ci va solo l'hash. Qui si prova il
tratto fino alla sessione; l'URL e il markup stanno in ``test_dashboard_elenco``.

1. **L'icona non decide niente.** Assente, ``null``, fuori forma: la guild resta
   autorizzata, e un'icona valida non fa entrare una guild che non lo sarebbe.
2. **La forma.** Un hash che non passa ``HASH_ICONA`` si scarta, e con lui ogni
   stringa che in un URL vorrebbe dire qualcos'altro.
3. **I tre casi della sessione**, gli stessi dei nomi.
4. **Il budget, a gradini.** Le icone cadono prima dei nomi, e mai a meta'.

Nessun hash reale: sono stringhe inventate nella forma giusta.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from dashboard import auth
from tests.sessione_dashboard import dati_di_sessione, entra, senza_variabili_di_database
from tests.test_dashboard_nome_server import (
    FUCINA,
    _accedi,
    _api_con_molte_guild,
    _app,
    _sessione_dal_cookie,
    _voce,
)
from tools.fixture_api import GUILD_MATURE, GUILD_TODAY

STATICA = "0123456789abcdef0123456789abcdef"
ANIMATA = "a_fedcba9876543210fedcba9876543210"


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    senza_variabili_di_database(monkeypatch)


def _con_icona(gid: int, icona, **kwargs) -> dict:
    voce = _voce(gid, **kwargs)
    if icona is not ...:
        voce["icon"] = icona
    return voce


# --- 1. l'icona non decide niente ----------------------------------------------


@pytest.mark.parametrize("icona", [STATICA, ANIMATA])
def test_un_hash_valido_si_raccoglie(icona):
    esito = auth.guild_autorizzate([_con_icona(GUILD_TODAY, icona)], {GUILD_TODAY})

    assert esito.guilds == {GUILD_TODAY}
    assert esito.icone == {GUILD_TODAY: icona}


@pytest.mark.parametrize(
    "icona",
    [
        ...,  # campo assente
        None,  # il server senza icona: Discord manda null
        "",
        123,
        ["x"],
        STATICA.upper(),  # Discord scrive minuscolo
        STATICA[:-1],  # 31 cifre
        STATICA + "0",  # 33 cifre
        "b_" + STATICA,  # un prefisso che Discord non documenta
        STATICA + "\n",  # "$" di re accetterebbe il ritorno a capo: fullmatch no
        "../../avatars/1/" + STATICA,
        STATICA + ".png?x=",
    ],
)
def test_un_icona_assente_o_fuori_forma_non_toglie_l_autorizzazione(icona):
    esito = auth.guild_autorizzate([_con_icona(GUILD_TODAY, icona)], {GUILD_TODAY})

    assert esito.guilds == {GUILD_TODAY}
    assert esito.icone == {}


def test_un_icona_non_fa_entrare_una_guild_non_osservata():
    esito = auth.guild_autorizzate([_con_icona(GUILD_TODAY, STATICA)], {GUILD_MATURE})

    assert esito.guilds == frozenset()
    assert esito.icone == {}


def test_un_icona_non_fa_entrare_chi_non_e_amministratore():
    voce = _con_icona(GUILD_TODAY, STATICA, permissions="0")

    esito = auth.guild_autorizzate([voce], {GUILD_TODAY})

    assert esito.guilds == frozenset()
    assert esito.icone == {}


# --- 3. i tre casi della sessione ---------------------------------------------


def test_chiave_assente_sessione_valida_senza_icone():
    # Il cookie firmato prima del deploy delle icone.
    sessione = auth.leggi_sessione(dati_di_sessione([GUILD_TODAY], nomi={GUILD_TODAY: FUCINA}))

    assert sessione is not None
    assert sessione.icone == {}
    assert sessione.nomi == {GUILD_TODAY: FUCINA}


@pytest.mark.parametrize(
    "icone",
    [
        [STATICA],
        STATICA,
        {"non-un-id": STATICA},
        {str(GUILD_TODAY): 5},
        {str(GUILD_TODAY): None},
        # Un valore che in scrittura non sarebbe mai passato: la lettura lo
        # ricontrolla, perche' e' questa stringa che finisce in un URL.
        {str(GUILD_TODAY): "https://evil.example/x.png"},
        {str(GUILD_TODAY): STATICA.upper()},
    ],
)
def test_icone_fuori_forma_valgono_nessuna_sessione(icone):
    dati = dati_di_sessione([GUILD_TODAY])
    dati["icone"] = icone

    assert auth.leggi_sessione(dati) is None


def test_un_icona_per_una_guild_non_autorizzata_invalida_la_sessione():
    dati = dati_di_sessione([GUILD_TODAY], icone={GUILD_MATURE: STATICA})

    assert auth.leggi_sessione(dati) is None


def test_le_icone_valide_tornano_con_le_chiavi_intere():
    dati = dati_di_sessione([GUILD_TODAY, GUILD_MATURE], icone={GUILD_TODAY: ANIMATA})

    sessione = auth.leggi_sessione(dati)

    assert sessione is not None
    assert sessione.icone == {GUILD_TODAY: ANIMATA}


# --- il callback, e il ricontrollo ---------------------------------------------


def test_il_login_scrive_le_icone_accanto_ai_nomi():
    voci = [_con_icona(GUILD_TODAY, STATICA, nome=FUCINA), _con_icona(GUILD_MATURE, None)]

    with TestClient(_app(voci), follow_redirects=False) as client:
        assert _accedi(client).status_code == 303
        sessione = _sessione_dal_cookie(client)

    assert sessione["guilds"] == sorted([GUILD_TODAY, GUILD_MATURE])
    assert sessione["icone"] == {str(GUILD_TODAY): STATICA}


def test_il_ricontrollo_riscrive_le_icone_e_toglie_quelle_sparite():
    # Il server ha tolto l'icona su Discord: dopo il ricontrollo la sessione non
    # la porta piu'. La chiave sparisce del tutto (nessuna icona = nessuna
    # chiave), che si legge come "mappa vuota".
    app = _app([_con_icona(GUILD_TODAY, None, nome=FUCINA)])
    with TestClient(app, follow_redirects=False) as client:
        entra(
            client,
            dati_di_sessione(
                [GUILD_TODAY],
                checked_at=time.time() - auth.TTL_RICONTROLLO_SECONDI - 1,
                nomi={GUILD_TODAY: FUCINA},
                icone={GUILD_TODAY: STATICA},
            ),
        )
        assert client.get(f"/guilds/{GUILD_TODAY}").status_code == 303
        assert _accedi(client).status_code == 303
        sessione = _sessione_dal_cookie(client)

    assert sessione["nomi"] == {str(GUILD_TODAY): FUCINA}
    assert "icone" not in sessione


# --- 4. il budget, a gradini ---------------------------------------------------


def _login_con(quante: int, *, nome: str = "n" * auth.LUNGHEZZA_MASSIMA_NOME) -> dict:
    transport, ids = _api_con_molte_guild(quante)
    voci = [_con_icona(gid, ANIMATA, nome=nome) for gid in ids]
    with TestClient(_app(voci, api_transport=transport), follow_redirects=False) as client:
        assert _accedi(client).status_code == 303
        return _sessione_dal_cookie(client)


def _gradino(sessione: dict) -> tuple[str, ...]:
    return tuple(k for k in ("nomi", "icone") if k in sessione)


def test_i_gradini_del_budget_nell_ordine():
    # Con nomi di 100 caratteri: pochi server hanno tutto, qualcuno di piu' perde
    # le icone e tiene i nomi, molti di piu' perdono tutto. Mai un gradino a
    # meta': quando una chiave c'e', c'e' per ogni guild.
    visti = []
    for quante in (1, 10, 14, 30):
        sessione = _login_con(quante)
        assert len(json.dumps(sessione).encode("utf-8")) <= auth.BUDGET_JSON_SESSIONE_BYTE
        for chiave in _gradino(sessione):
            assert len(sessione[chiave]) == quante, (quante, chiave)
        visti.append(_gradino(sessione))

    assert visti == [("nomi", "icone"), ("nomi", "icone"), ("nomi",), ()]


def test_le_icone_non_restano_mai_senza_nomi():
    # Guild senza nome ma con icona: niente da scrivere, nemmeno le icone.
    transport, ids = _api_con_molte_guild(2)
    voci = [_con_icona(gid, STATICA, nome=...) for gid in ids]

    with TestClient(_app(voci, api_transport=transport), follow_redirects=False) as client:
        assert _accedi(client).status_code == 303
        sessione = _sessione_dal_cookie(client)

    assert "nomi" not in sessione
    assert "icone" not in sessione
