"""Backfill di ``members``: funzione interna del bot, non un comando.

Quando Kindling entra in un server, i membri gia' presenti non hanno mai
prodotto un evento ``member_join``: il loro ``joined_at`` esiste solo nell'API
Discord, e se qualcuno di loro esce prima che qualcuno lo legga quel dato e'
perso per sempre. Il backfill lo recupera una volta sola per guild.

**Perche' non e' piu' un comando** (prima: ``!backfill_members``, admin only).
``is_survivors_only`` distingue i membri backfillati dagli osservati
confrontando il loro ``joined_at`` con ``guilds.first_seen_at``. Un backfill
eseguito su una guild gia' osservata riscriverebbe il ``joined_at`` di membri
osservati con quello storico letto dall'API, rompendo quella distinzione **senza
nessun errore**: i numeri continuerebbero a uscire, semplicemente sbagliati. Una
regola che dipende da "nessuno lo rilancia" e' piu' debole di un comando che non
esiste.

Il secondo livello e' in ``db.insert_member_join_if_absent``: anche eseguito
comunque, il backfill non puo' toccare una riga esistente. La garanzia non sta
nel codice che chiama, esattamente come per il vincolo di soppressione delle
metriche.

**Perche' anche all'avvio e non solo su ``on_guild_join``.** Il flusso OAuth che
aggiunge l'applicazione a un server funziona anche a bot spento, e Discord non
ritrasmette gli eventi del gateway: una guild aggiunta durante un downtime non
produrrebbe mai quell'evento, e i ``joined_at`` dei suoi membri sarebbero persi.
All'avvio quindi non ci si fida di aver visto gli eventi, si guarda lo stato —
lo stesso pattern della riconciliazione vocale (modello-grafo.md 4.4).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import db

logger = logging.getLogger(__name__)


async def ensure_backfilled(guild) -> bool:
    """Registra la guild e, se non e' gia' stato fatto, ne backfilla i membri.

    Ritorna ``True`` se il backfill e' stato eseguito adesso. Il marcatore e'
    ``guilds.backfilled_at``: su una guild gia' backfillata non viene nemmeno
    letta la member list, che e' una chiamata costosa all'API Discord.
    """
    record = await db.register_guild(
        guild_id=guild.id, first_seen_at=datetime.now(timezone.utc)
    )
    if record.backfilled_at is not None:
        logger.debug(
            "guild_id=%s gia' backfillata il %s: niente da fare",
            guild.id,
            record.backfilled_at,
        )
        return False

    inserted, skipped = await _backfill_members(guild)
    await db.mark_guild_backfilled(guild_id=guild.id)
    logger.info(
        "Backfill members completato per guild_id=%s: %d inseriti, %d ignorati",
        guild.id,
        inserted,
        skipped,
    )
    return True


async def _backfill_members(guild) -> tuple[int, int]:
    """Inserisce i membri attuali non ancora presenti in ``members``.

    ``fetch_members`` chiama l'API Discord direttamente (non la cache):
    richiede l'intent privilegiato Server Members, gia' abilitato in
    ``bot/client.py`` e nel Developer Portal.
    """
    inserted = 0
    skipped = 0
    async for member in guild.fetch_members(limit=None):
        if member.bot or member.joined_at is None:
            skipped += 1
            continue
        # Mai upsert_member_join: quello sovrascriverebbe un ingresso osservato.
        if await db.insert_member_join_if_absent(
            guild_id=guild.id, author_id=member.id, joined_at=member.joined_at
        ):
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


async def reconcile_guilds(bot) -> None:
    """All'avvio: guarda lo stato di ogni guild in cui il bot si trova.

    Una guild che fallisce non blocca le altre — un errore dell'API Discord su
    un server non deve lasciare gli altri senza backfill, e il tentativo si
    ripete al riavvio successivo perche' il marcatore non e' stato scritto.
    """
    for guild in list(getattr(bot, "guilds", [])):
        try:
            await ensure_backfilled(guild)
        except Exception:
            logger.exception(
                "Backfill/registrazione falliti per guild_id=%s: le altre guild proseguono",
                getattr(guild, "id", None),
            )
