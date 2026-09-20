"""Il nome del server, dalla sessione (stato-progetto.md 7-R).

I nomi arrivano da ``GET /users/@me/guilds``, viaggiano nel cookie di sessione e
finiscono in testata, nel titolo della scheda e nell'elenco dei server. Non
esiste una colonna ``name`` su ``guilds``, e questo commit non ne aggiunge una.

Cosa prova ogni gruppo di test, e perche' senza non se ne accorgerebbe nessuno:

1. **Il nome non decide niente.** Una guild senza ``name`` resta autorizzata; una
   guild con un bel nome ma non osservata resta fuori. Se il nome entrasse nella
   decisione — per concedere o per negare — l'errore non darebbe nessun segnale:
   l'insieme autorizzato e' un insieme di interi, e un intero in piu' o in meno
   si vede solo guardando.
2. **I tre casi di ``leggi_sessione``**, che sono tre cose diverse e vanno tenute
   tali: chiave assente (valida, senza nomi), chiave fuori forma (nessuna
   sessione), nomi che divergono dall'insieme autorizzato (nessuna sessione).
3. **Il cookie di ieri.** E' l'unico caso che in produzione non si puo' riprovare:
   colpisce ogni persona collegata nel momento del deploy, una volta sola.
4. **Il limite di lunghezza e il ripiego totale.** Non scatteranno con un server
   osservato; esistono perche' il giorno in cui scatteranno nessuno stara'
   guardando.
5. **Il ripiego in pagina.** Con ``StrictUndefined`` la differenza fra "ripiego"
   e "errore 500" e' una riga.

Nessun id e nessun nome di server reale: gli id vengono dal fixture, i nomi sono
inventati qui.
"""

from __future__ import annotations

import json
import time
from base64 import b64decode
from typing import Any, Callable, Optional

import httpx
import itsdangerous
import pytest
from fastapi.testclient import TestClient

from dashboard import auth, config
from dashboard.main import crea_app
from tests.sessione_dashboard import (
    OAUTH_DI_TEST,
    client_autenticato,
    dati_di_sessione,
    entra,
)
from tools.fixture_api import GUILD_EDGE, GUILD_MATURE, GUILD_TODAY
from tools.fixture_api import app as fixture_app

# Nomi inventati, come gli id del fixture: qui non entra niente di reale.
FUCINA = "Fucina dei Corvi"
BORGO = "Borgo di Mezzanotte"
VELE = "Le Vele Lente"

NOMI_DI_PROVA = {GUILD_TODAY: FUCINA, GUILD_MATURE: BORGO, GUILD_EDGE: VELE}


@pytest.fixture(autouse=True)
def _nessuna_variabile_di_database(monkeypatch):
    for nome in config.DATABASE_VARIABLES:
        monkeypatch.delenv(nome, raising=False)


# --- Discord finto, ridotto all'osso ------------------------------------------


def _voce(gid: int, *, nome: Any = "x", permissions: str = "8") -> dict:
    voce = {"id": str(gid), "owner": False, "permissions": permissions}
    if nome is not ...:
        voce["name"] = nome
    return voce


def discord_finto(voci: list) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v10/oauth2/token":
            return httpx.Response(200, json={"access_token": "token-finto", "token_type": "Bearer"})
        if request.url.path == "/api/v10/users/@me/guilds":
            return httpx.Response(200, json=voci)
        return httpx.Response(404)

    return handler


def _app(voci: Optional[list] = None, api_transport: Optional[httpx.AsyncBaseTransport] = None):
    return crea_app(
        api_http=httpx.AsyncClient(
            transport=api_transport or httpx.ASGITransport(app=fixture_app),
            base_url="http://api.test",
        ),
        oauth=OAUTH_DI_TEST,
        discord_http=auth.crea_discord_http(
            transport=httpx.MockTransport(discord_finto(voci if voci is not None else []))
        ),
    )


def _sessione_dal_cookie(client: TestClient) -> dict:
    """Il cookie di sessione e' firmato, non cifrato: si legge."""
    valore = client.cookies.get(auth.COOKIE_SESSIONE)
    assert valore, "nessun cookie di sessione"
    firmato = itsdangerous.TimestampSigner(OAUTH_DI_TEST.session_secret).unsign(valore)
    return json.loads(b64decode(firmato))


def _contesto_in_testata(html: str) -> str:
    """Cosa dice la riga di contesto della testata: il nome, o l'ID di ripiego."""
    apertura = '<span class="contesto-guild"><span>server</span><code>'
    assert apertura in html, "nessuna riga di contesto in testata"
    resto = html.split(apertura, 1)[1]
    return resto.split("</code>", 1)[0]


def _accedi(client: TestClient) -> httpx.Response:
    avvio = client.post("/login")
    assert avvio.status_code == 303
    from urllib.parse import parse_qs, urlsplit

    state = parse_qs(urlsplit(avvio.headers["location"]).query)["state"][0]
    return client.get("/oauth/callback", params={"code": "code-finto", "state": state})


# --- 1. il nome non decide niente ---------------------------------------------


def test_una_guild_senza_nome_resta_autorizzata():
    # Il lato "per negare": un ``name`` che manca non toglie l'autorizzazione.
    # A differenza di id e permissions, un nome fuori forma non fa nemmeno alzare.
    risposta = [_voce(GUILD_TODAY, nome=...), _voce(GUILD_MATURE, nome=FUCINA)]

    esito = auth.guild_autorizzate(risposta, {GUILD_TODAY, GUILD_MATURE})

    assert esito.guilds == {GUILD_TODAY, GUILD_MATURE}
    assert esito.nomi == {GUILD_MATURE: FUCINA}


@pytest.mark.parametrize("nome", [None, 42, [], {"a": 1}, "", "   "])
def test_un_nome_fuori_forma_non_toglie_l_autorizzazione(nome):
    esito = auth.guild_autorizzate([_voce(GUILD_TODAY, nome=nome)], {GUILD_TODAY})

    assert esito.guilds == {GUILD_TODAY}
    assert esito.nomi == {}


def test_un_nome_non_fa_entrare_una_guild_non_osservata():
    # Il lato "per concedere": il nome piu' bello del mondo non fa entrare una
    # guild che Kindling non osserva, ne' una su cui non si e' amministratori.
    risposta = [_voce(999, nome=FUCINA), _voce(GUILD_MATURE, nome=BORGO, permissions="0")]

    esito = auth.guild_autorizzate(risposta, {GUILD_MATURE})

    assert esito.guilds == frozenset()
    assert esito.nomi == {}


def test_un_id_fuori_forma_alza_anche_se_il_nome_e_a_posto():
    # Invariato: id e permissions restano i dati su cui si decide, e fuori forma
    # fanno alzare. E' la differenza che questo commit introduce, e va vista.
    with pytest.raises(auth.RispostaDiscordNonValida):
        auth.guild_autorizzate([{"id": 42, "name": FUCINA, "permissions": "8"}], {42})


def test_l_esito_non_si_valuta_come_booleano():
    # ``if not autorizzate`` era una riga vera di completa_callback quando questa
    # funzione restituiva un insieme. Su un oggetto sarebbe sempre falsa, e un
    # insieme vuoto passerebbe in silenzio.
    esito = auth.guild_autorizzate([], {GUILD_TODAY})
    assert esito.guilds == frozenset()
    with pytest.raises(TypeError, match="booleana"):
        bool(esito)


# --- 2. i tre casi di leggi_sessione ------------------------------------------


def test_chiave_assente_sessione_valida_senza_nomi():
    sessione = auth.leggi_sessione(dati_di_sessione([GUILD_TODAY]))

    assert sessione is not None
    assert sessione.guilds == {GUILD_TODAY}
    assert sessione.nomi == {}


@pytest.mark.parametrize(
    "nomi",
    [
        "non una mappa",
        [],
        42,
        None,
        {"non-un-numero": FUCINA},
        {str(GUILD_TODAY): 42},
        {str(GUILD_TODAY): None},
    ],
)
def test_nomi_fuori_forma_valgono_nessuna_sessione(nomi):
    dati = dati_di_sessione([GUILD_TODAY])
    dati["nomi"] = nomi

    # leggi_sessione non alza MAI: l'esito e' None, non un'eccezione. E' la
    # differenza di meccanismo con guild_autorizzate, che invece alza.
    assert auth.leggi_sessione(dati) is None


def test_un_nome_per_una_guild_non_autorizzata_invalida_la_sessione():
    # Terzo caso, il meno ovvio: questi dati li scriviamo noi, e guilds e nomi
    # si scrivono insieme dopo un clear(). Se divergono, o la firma l'ha prodotta
    # qualcun altro o il difetto e' nostro: "tengo il resto" vorrebbe dire
    # continuare su dati che abbiamo appena dimostrato di non capire.
    dati = dati_di_sessione([GUILD_TODAY], nomi={GUILD_TODAY: FUCINA, GUILD_MATURE: BORGO})

    assert auth.leggi_sessione(dati) is None


def test_i_nomi_validi_tornano_con_le_chiavi_intere():
    dati = dati_di_sessione([GUILD_TODAY, GUILD_MATURE], nomi={GUILD_TODAY: FUCINA})

    sessione = auth.leggi_sessione(dati)

    # Le chiavi in JSON sono stringhe; qui sono interi, come sessione.guilds.
    assert sessione is not None
    assert sessione.nomi == {GUILD_TODAY: FUCINA}


# --- 3. il cookie di ieri ------------------------------------------------------


def test_il_cookie_firmato_prima_del_deploy_continua_a_funzionare():
    """Nessuno viene buttato fuori da un cambio di formato.

    ``dati_di_sessione()`` senza ``nomi`` produce esattamente il cookie che le
    persone collegate avevano in mano al momento del deploy: la vista si apre, e
    la testata mostra l'ID finche' il ricontrollo dei 15 minuti non riscrive la
    sessione con i nomi.
    """
    with client_autenticato(_app()) as client:
        risposta = client.get(f"/guilds/{GUILD_TODAY}")

    assert risposta.status_code == 200
    # L'ancora e' la riga di contesto in testata, non la parola "None" nella
    # pagina: il blocco "Parametri" di Stato contiene dei None suoi, legittimi,
    # e un controllo scritto su quelli direbbe di si' per la ragione sbagliata.
    assert _contesto_in_testata(risposta.text) == str(GUILD_TODAY)


def test_il_ricontrollo_silenzioso_porta_i_nomi_a_una_sessione_che_non_li_aveva():
    """L'invalidazione della cache dei nomi e' il ricontrollo, non un meccanismo a parte.

    Un server rinominato — o una sessione aperta prima del deploy — prende il
    nome nuovo entro un quarto d'ora, da solo.
    """
    app = _app([_voce(GUILD_TODAY, nome=FUCINA)])
    with TestClient(app, follow_redirects=False) as client:
        # Sessione vecchio formato, con il ricontrollo gia' scaduto.
        entra(client, dati_di_sessione([GUILD_TODAY], checked_at=time.time() - auth.TTL_RICONTROLLO_SECONDI - 1))
        assert "nomi" not in _sessione_dal_cookie(client)

        verso_discord = client.get(f"/guilds/{GUILD_TODAY}")
        assert verso_discord.status_code == 303
        assert verso_discord.headers["location"].startswith(auth.DISCORD_AUTHORIZE_URL)
        assert _accedi(client).status_code == 303

        assert _sessione_dal_cookie(client)["nomi"] == {str(GUILD_TODAY): FUCINA}
        assert FUCINA in client.get(f"/guilds/{GUILD_TODAY}").text


# --- 4. il limite di lunghezza e il ripiego totale ----------------------------


def test_un_nome_oltre_il_limite_si_tronca_in_scrittura():
    # 100 caratteri e' il massimo che Discord dichiara per il nome di una guild
    # (campo ``name``, https://discord.com/developers/docs/resources/guild,
    # letto il 20/09/2026): il troncamento e' una rete contro una risposta che
    # violi il proprio limite.
    lunghissimo = "a" * 250

    esito = auth.guild_autorizzate([_voce(GUILD_TODAY, nome=lunghissimo)], {GUILD_TODAY})

    assert esito.nomi[GUILD_TODAY] == "a" * auth.LUNGHEZZA_MASSIMA_NOME
    assert auth.LUNGHEZZA_MASSIMA_NOME == 100


def _api_con_molte_guild(quante: int) -> tuple[httpx.MockTransport, list[int]]:
    """Un'API finta che osserva ``quante`` guild, per riempire il cookie."""
    ids = [GUILD_TODAY + i for i in range(quante)]

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/guilds":
            return httpx.Response(
                200,
                json=[{"guild_id": gid, "first_seen_at": "2026-01-01T00:00:00Z"} for gid in ids],
            )
        return httpx.Response(404)

    return httpx.MockTransport(api), ids


def test_oltre_il_budget_del_cookie_nessun_nome_non_quasi_nessuno():
    # O tutti o nessuno: un elenco con tre nomi e due numeri sembra un difetto,
    # un ripiego totale sugli ID si spiega da se'.
    transport, ids = _api_con_molte_guild(40)
    voci = [_voce(gid, nome="n" * auth.LUNGHEZZA_MASSIMA_NOME) for gid in ids]

    with TestClient(_app(voci, api_transport=transport), follow_redirects=False) as client:
        assert _accedi(client).status_code == 303
        sessione = _sessione_dal_cookie(client)

    assert len(sessione["guilds"]) == 40
    assert "nomi" not in sessione, "il ripiego deve essere totale, non parziale"
    # E il cookie che ne esce sta davvero nel budget che il ripiego difende.
    assert len(json.dumps(sessione).encode("utf-8")) <= auth.BUDGET_JSON_SESSIONE_BYTE


def test_dentro_il_budget_i_nomi_ci_sono_tutti():
    # La sponda dell'altro test: senza, "nessun nome" resterebbe verde anche se
    # il budget fosse sbagliato al punto da non far passare mai niente.
    transport, ids = _api_con_molte_guild(5)
    voci = [_voce(gid, nome=f"{FUCINA} {i}") for i, gid in enumerate(ids)]

    with TestClient(_app(voci, api_transport=transport), follow_redirects=False) as client:
        assert _accedi(client).status_code == 303
        sessione = _sessione_dal_cookie(client)

    assert len(sessione["nomi"]) == 5


# --- 5. il nome in pagina, e il ripiego ---------------------------------------


def _con_nomi(**kwargs) -> TestClient:
    client = TestClient(_app(), **kwargs)
    entra(client, dati_di_sessione(nomi=NOMI_DI_PROVA))
    return client


def test_la_testata_e_il_titolo_della_scheda_portano_il_nome():
    with _con_nomi() as client:
        html = client.get(f"/guilds/{GUILD_TODAY}/robustezza").text

    assert f"<title>Robustezza · {FUCINA} — Kindling</title>" in html
    assert _contesto_in_testata(html) == FUCINA
    # Il sottotitolo sotto l'h1 era la stessa cosa a dieci centimetri di distanza.
    assert 'class="sottotitolo"' not in html


def test_senza_nomi_la_testata_ripiega_sull_id_e_non_esplode():
    # Il ripiego e' il setdefault di cornice(), gia' li' dal commit della
    # cornice: pagina() non imposta niente quando il nome non c'e'. Con
    # StrictUndefined, "niente" e "None" sono due pagine molto diverse.
    with client_autenticato(_app()) as client:
        html = client.get(f"/guilds/{GUILD_TODAY}/robustezza").text

    assert f"<title>Robustezza · {GUILD_TODAY} — Kindling</title>" in html
    assert _contesto_in_testata(html) == str(GUILD_TODAY)


def test_una_guild_senza_nome_ripiega_da_sola_mentre_le_altre_lo_hanno():
    # La mappa dei nomi puo' essere parziale per guild (una guild senza ``name``
    # su Discord), ed e' diverso dal ripiego totale per budget: qui il ripiego
    # e' per riga.
    with TestClient(_app()) as client:
        entra(client, dati_di_sessione(nomi={GUILD_TODAY: FUCINA}))
        con_nome = client.get(f"/guilds/{GUILD_TODAY}/coorti").text
        senza_nome = client.get(f"/guilds/{GUILD_MATURE}/coorti").text

    assert _contesto_in_testata(con_nome) == FUCINA
    assert _contesto_in_testata(senza_nome) == str(GUILD_MATURE)


def test_l_elenco_dei_server_mostra_i_nomi_con_l_id_sotto():
    with _con_nomi() as client:
        html = client.get("/").text

    for gid, nome in NOMI_DI_PROVA.items():
        assert f'<a href="/guilds/{gid}">{nome}</a>' in html
        assert f"<code>{gid}</code>" in html


def test_l_elenco_senza_nomi_resta_quello_di_prima():
    with client_autenticato(_app()) as client:
        html = client.get("/").text

    assert f'<a href="/guilds/{GUILD_TODAY}">{GUILD_TODAY}</a>' in html


def test_un_nome_con_del_markup_non_lo_inietta():
    # autoescape e' gia' acceso in crea_templates: qui si verifica che lo sia
    # anche su questo cammino, senza aggiungere escaping a mano da nessuna parte
    # (sarebbe doppio, e il doppio escaping si vede).
    cattivo = "<script>alert(1)</script>"
    with TestClient(_app()) as client:
        entra(client, dati_di_sessione(nomi={GUILD_TODAY: cattivo}))
        html = client.get(f"/guilds/{GUILD_TODAY}").text

    assert cattivo not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
