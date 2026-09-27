"""L'elenco dei server, la pagina ``/`` (dashboard.md 4, «L'elenco dei server, in dettaglio»).

Qui si decide cosa dice ogni riga e in che ordine stanno le righe; il template
mette soltanto in pagina. Come per le viste: l'ordine, le date e l'URL
dell'icona sono decisioni, e una decisione presa in un template non si prova
senza rendere HTML.

Una sola chiamata all'API, ``guilds()``, e nessuna per singolo server: niente
stato di salute, pillole o conteggi presi dalle viste. In un elenco
servirebbero a confrontare i server fra loro, cioe' sarebbero un punteggio
sintetico travestito (invariante 3).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Optional, Sequence

from api.models import GuildRow

from . import auth, stato

# L'unica origine altrui della dashboard, e solo per le immagini: la stessa che
# la CSP ammette in img-src (main.CSP). Un test tiene le due cose allineate.
CDN_DISCORD = "https://cdn.discordapp.com"

# L'icona si mostra a 40 px. 128 e non 80 (il 2x esatto): Discord documenta per
# ``size`` solo le potenze di due fra 16 e 4096. Provato il 27/09/2026: 80 oggi
# risponde 200, 81 risponde 400, cioe' 80 sta in una lista che la
# documentazione non dichiara. 128 e' documentato, e il browser lo scala.
LATO_ICONA_PX = 40
DIMENSIONE_ICONA = 128

# Ogni quanto l'elenco si aggiorna da solo: e' il ricontrollo silenzioso, che
# riscrive in sessione insieme autorizzato, nomi e icone. Preso dal TTL e non
# scritto a mano nella pagina, dove resterebbe vero fino al primo che lo cambia.
MINUTI_RICONTROLLO, _SECONDI_IN_PIU = divmod(auth.TTL_RICONTROLLO_SECONDI, 60)
# "ogni 15 minuti" e' una frase che regge solo un numero intero di minuti: un
# TTL che non lo e' va detto in un altro modo, non arrotondato in silenzio.
assert _SECONDI_IN_PIU == 0, "TTL_RICONTROLLO_SECONDI non e' un numero intero di minuti"


# --- l'icona -----------------------------------------------------------------


def url_icona(guild_id: int, hash_icona: str) -> str:
    """L'URL dell'icona sul CDN, composto qui e solo da un intero e da un hash valido.

    WebP **statico** anche per gli hash animati (``a_...``): niente animazioni in
    un elenco. Per la documentazione di Discord un WebP e' animato solo con
    ``?animated=true``, e verificato sul CDN il 27/09/2026 con un hash ``a_``
    vero: senza il parametro arriva un solo fotogramma.

    Alza su un input fuori forma invece di comporre qualcosa: nessun chiamante
    legittimo ne passa uno, perche' la sessione li ha gia' scartati.
    """
    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError(f"guild_id non e' un intero positivo: {guild_id!r}")
    if not isinstance(hash_icona, str) or not auth.HASH_ICONA.fullmatch(hash_icona):
        raise ValueError(f"hash d'icona fuori forma: {hash_icona!r}")
    return f"{CDN_DISCORD}/icons/{guild_id}/{hash_icona}.webp?size={DIMENSIONE_ICONA}"


def monogramma(nome: Optional[str]) -> str:
    """La lettera che sta sotto l'icona, sempre: la prima lettera o cifra del nome.

    ``#`` senza nome. La prima alfanumerica e non il primo carattere: un nome
    che comincia con ``[`` o con un'emoji avrebbe per monogramma una parentesi.
    """
    if not nome:
        return "#"
    nome = unicodedata.normalize("NFC", nome)
    for carattere in nome:
        if carattere.isalnum():
            return carattere.upper()
    return nome[0]


# --- l'ordine ------------------------------------------------------------------


def _senza_accenti(testo: str) -> str:
    scomposto = unicodedata.normalize("NFKD", testo)
    return "".join(c for c in scomposto if not unicodedata.combining(c)).casefold()


def chiave(guild_id: int, nome: Optional[str]) -> tuple:
    """La chiave dell'ordine dell'elenco. Totale: due righe diverse non pareggiano mai.

    1. Le righe senza nome in fondo.
    2. Il nome senza accenti e con ``casefold()``: con ``casefold()`` da solo
       «Élite» finirebbe dopo «Zefiro», perche' ``é`` viene dopo ``z`` in
       Unicode. Le iniziali minuscole contano come maiuscole.
    3. Il nome con ``casefold()`` e basta, perche' «Elite» ed «Élite» non
       pareggino.
    4. L'ID. Due server con lo stesso nome devono avere un ordine deciso, non
       quello in cui li manda l'API: e' la lezione dell'``ORDER BY`` senza
       tiebreaker (CLAUDE.md 7). Per le righe senza nome e' l'unica chiave.
    """
    if not nome:
        return (1, "", "", guild_id)
    return (0, _senza_accenti(nome), nome.casefold(), guild_id)


def ordina(guilds: Sequence[GuildRow], nomi: Mapping[int, str]) -> list[GuildRow]:
    """Le guild nell'ordine dell'elenco. Funzione pura: niente orologio, niente sessione."""
    return sorted(guilds, key=lambda g: chiave(g.guild_id, nomi.get(g.guild_id)))


# --- le date -------------------------------------------------------------------


def data(valore: datetime, oggi: date) -> str:
    """``30 ago``, a Roma; ``3 mar 2025`` se l'anno non e' quello di ``oggi``."""
    giorno = stato.giorno(valore)
    testo = stato.data_breve(giorno)
    return testo if giorno.year == oggi.year else f"{testo} {giorno.year}"


def data_estesa(valore: datetime, oggi: date) -> str:
    """``12 settembre``, a Roma, con l'anno alle stesse condizioni di ``data``."""
    giorno = stato.giorno(valore)
    testo = stato.data_estesa(giorno)
    return testo if giorno.year == oggi.year else f"{testo} {giorno.year}"


def _con_preposizione(preposizione: str, testo_data: str) -> str:
    """``dal 12 settembre``, ma ``dall'8 settembre`` e ``dall'11 settembre``.

    ``preposizione`` e' la forma articolata davanti a consonante (``dal``,
    ``al``): davanti a «otto» e «undici» si elide, come si dice.
    """
    if testo_data.split(" ", 1)[0] in ("8", "11"):
        return f"{preposizione}l'{testo_data}"
    return f"{preposizione} {testo_data}"


def durata(dal_giorno: date, oggi: date) -> str:
    """Da quanto, in giorni di CALENDARIO a Roma, con l'unita' che si legge meglio.

    ``oggi``; ``1 giorno`` / ``N giorni`` sotto la settimana; ``1 settimana`` /
    ``N settimane`` sotto le 26; ``N mesi`` di calendario compiuti sotto i 24;
    ``N anni`` da li'. Una data nel futuro (orologi disallineati) vale ``oggi``.
    """
    giorni = (oggi - dal_giorno).days
    if giorni <= 0:
        return "oggi"
    if giorni < 7:
        return "1 giorno" if giorni == 1 else f"{giorni} giorni"
    settimane = giorni // 7
    if settimane < 26:
        return "1 settimana" if settimane == 1 else f"{settimane} settimane"
    mesi = (oggi.year - dal_giorno.year) * 12 + oggi.month - dal_giorno.month
    if oggi.day < dal_giorno.day:
        mesi -= 1
    if mesi < 24:
        return f"{mesi} mesi"
    return f"{mesi // 12} anni"


# --- le righe ----------------------------------------------------------------


@dataclass(frozen=True)
class Fatto:
    """Un fatto etichetta/valore: il valore, e sotto il dettaglio (se c'e')."""

    valore: str
    dettaglio: Optional[str]
    # Il calcolo che non c'e' ancora: il valore si scrive in tono spento, perche'
    # non e' un dato ma la sua assenza.
    in_attesa: bool = False


@dataclass(frozen=True)
class Riga:
    guild_id: int
    nome: Optional[str]
    monogramma: str
    icona: Optional[str]  # l'URL, gia' composto
    osserva: Fatto
    calcolo: Fatto
    nota_uscita: Optional[str]


def uscito(guild: GuildRow) -> bool:
    """Il bot e' stato tolto dal server e non e' rientrato.

    ``left_at`` e ``rejoined_at`` entrambi valorizzati sono un rientro: nessuna
    nota in questa pagina, il buco di osservazione lo dichiara Stato.
    """
    return guild.left_at is not None and guild.rejoined_at is None


def riga(guild: GuildRow, nome: Optional[str], hash_icona: Optional[str], oggi: date) -> Riga:
    fuori = uscito(guild)

    osserva = Fatto(
        data(guild.first_seen_at, oggi),
        # "fino al 12 set", e "fino all'8 set".
        "fino " + _con_preposizione("al", data(guild.left_at, oggi)) if fuori
        else durata(stato.giorno(guild.first_seen_at), oggi),
    )

    if guild.latest_metrics_as_of is not None:
        giorno_calcolo = stato.giorno(guild.latest_metrics_as_of)
        calcolo = Fatto(data(guild.latest_metrics_as_of, oggi), stato.GIORNI[giorno_calcolo.weekday()])
    elif fuori:
        # "arriva con il primo calcolo" potrebbe essere falso: dopo l'uscita
        # arriva al piu' un calcolo, il lunedi' seguente, poi niente. "nessuno"
        # descrive il momento e non promette niente; la nota spiega il perche'.
        calcolo = Fatto("nessuno", None, in_attesa=True)
    else:
        # Senza data: il prossimo calcolo richiede la cadenza, che si misura
        # sulle run, e l'elenco non le legge.
        calcolo = Fatto("non ancora", "arriva con il primo calcolo settimanale", in_attesa=True)

    nota = None
    if fuori:
        nota = (
            f"Il bot non è più nel server {_con_preposizione('dal', data_estesa(guild.left_at, oggi))}: "
            "i dati si fermano a quel giorno."
        )

    return Riga(
        guild_id=guild.guild_id,
        nome=nome or None,
        monogramma=monogramma(nome),
        icona=url_icona(guild.guild_id, hash_icona) if hash_icona else None,
        osserva=osserva,
        calcolo=calcolo,
        nota_uscita=nota,
    )


def costruisci(
    guilds: Sequence[GuildRow],
    nomi: Mapping[int, str],
    icone: Mapping[int, str],
    *,
    adesso: Optional[datetime] = None,
) -> list[Riga]:
    """Le righe dell'elenco, nell'ordine dell'elenco.

    ``adesso`` e' per i test; senza, l'orologio e' quello di Stato
    (``stato.adesso``), cosi' le due pagine non possono avere un "oggi" diverso.
    """
    oggi = stato.giorno(adesso if adesso is not None else stato.adesso())
    return [
        riga(g, nomi.get(g.guild_id), icone.get(g.guild_id), oggi)
        for g in ordina(guilds, nomi)
    ]
