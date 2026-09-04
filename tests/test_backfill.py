"""Il backfill di ``members``: non invocabile, e incapace di fare danno.

Due livelli indipendenti, e i test li coprono separatamente perche' e' il punto
del disegno:

1. **Non e' un comando.** La logica e' interna al bot e nessuno — nemmeno un
   amministratore — puo' lanciarla a mano. Una regola che dipende da "nessuno lo
   rilancia" e' piu' debole di un comando che non esiste.
2. **Se venisse eseguito comunque, non potrebbe sovrascrivere niente.** Il
   percorso di backfill inserisce solo i membri assenti e non tocca mai una riga
   esistente, quindi non puo' rimpiazzare un ``joined_at`` osservato con quello
   storico letto dall'API Discord.

Perche' quel danno conta: ``is_survivors_only`` distingue i membri backfillati
dagli osservati confrontando ``joined_at`` con ``guilds.first_seen_at``. Un
backfill su una guild gia' osservata riscriverebbe il ``joined_at`` di membri
osservati, rompendo quella distinzione **senza nessun errore**.

Nessun database e nessun gateway: le funzioni di ``bot.db`` sono sostituite, e
gli oggetti Discord sono finti e espongono solo cio' che il codice legge.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot import backfill, db

T0 = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)


class FakeGuild:
    def __init__(self, guild_id: int, members: list[SimpleNamespace]) -> None:
        self.id = guild_id
        self.name = f"guild-{guild_id}"
        self._members = members
        self.fetch_calls = 0

    async def fetch_members(self, *, limit=None):
        self.fetch_calls += 1
        for member in self._members:
            yield member


def fake_member(member_id: int, *, joined_at=T0, is_bot: bool = False):
    return SimpleNamespace(id=member_id, bot=is_bot, joined_at=joined_at)


@pytest.fixture()
def fake_db(monkeypatch):
    """Sostituisce le scritture di ``bot.db`` e registra le chiamate."""
    calls = {"inserted": [], "upserted": [], "marked": [], "registered": []}
    state = {"first_seen_at": T0, "backfilled_at": None}

    async def register_guild(*, guild_id, first_seen_at):
        calls["registered"].append(guild_id)
        return db.GuildRecord(
            guild_id=guild_id,
            first_seen_at=state["first_seen_at"],
            backfilled_at=state["backfilled_at"],
        )

    async def insert_member_join_if_absent(*, guild_id, author_id, joined_at):
        calls["inserted"].append((guild_id, author_id, joined_at))
        return True

    async def upsert_member_join(*, guild_id, author_id, joined_at):
        calls["upserted"].append((guild_id, author_id, joined_at))

    async def mark_guild_backfilled(*, guild_id):
        calls["marked"].append(guild_id)

    monkeypatch.setattr(db, "register_guild", register_guild)
    monkeypatch.setattr(
        db, "insert_member_join_if_absent", insert_member_join_if_absent
    )
    monkeypatch.setattr(db, "upsert_member_join", upsert_member_join)
    monkeypatch.setattr(db, "mark_guild_backfilled", mark_guild_backfilled)
    return calls, state


def test_guild_gia_backfillata_non_viene_ribackfillata(fake_db):
    calls, state = fake_db
    state["backfilled_at"] = T0
    guild = FakeGuild(111, [fake_member(1), fake_member(2)])

    done = asyncio.run(backfill.ensure_backfilled(guild))

    assert done is False
    # Nemmeno la lettura dei membri: la chiamata all'API Discord e' proprio il
    # costo che il marcatore evita, oltre al danno che eviterebbe comunque il
    # livello 2.
    assert guild.fetch_calls == 0
    assert calls["inserted"] == []
    assert calls["marked"] == []


def test_guild_mai_backfillata_viene_backfillata_una_volta_sola(fake_db):
    calls, state = fake_db
    guild = FakeGuild(111, [fake_member(1), fake_member(2)])

    assert asyncio.run(backfill.ensure_backfilled(guild)) is True
    assert [author_id for _, author_id, _ in calls["inserted"]] == [1, 2]
    assert calls["marked"] == [111]

    # Al riavvio successivo il marcatore c'e' e non si rifa'.
    state["backfilled_at"] = T0
    assert asyncio.run(backfill.ensure_backfilled(guild)) is False
    assert guild.fetch_calls == 1


def test_il_backfill_non_aggiorna_mai_una_riga_esistente(fake_db):
    calls, _ = fake_db
    guild = FakeGuild(111, [fake_member(1)])

    asyncio.run(backfill.ensure_backfilled(guild))

    # Il percorso di backfill passa SOLO dall'inserimento condizionale: usare
    # upsert_member_join qui sovrascriverebbe il joined_at osservato di un
    # membro con quello storico, che e' esattamente il danno da rendere
    # impossibile.
    assert calls["inserted"] != []
    assert calls["upserted"] == []


def test_bot_e_membri_senza_data_di_ingresso_sono_ignorati(fake_db):
    calls, _ = fake_db
    guild = FakeGuild(
        111,
        [fake_member(1), fake_member(2, is_bot=True), fake_member(3, joined_at=None)],
    )

    asyncio.run(backfill.ensure_backfilled(guild))

    assert [author_id for _, author_id, _ in calls["inserted"]] == [1]


def test_allavvio_si_guarda_lo_stato_di_tutte_le_guild(fake_db):
    # on_guild_join scatta solo se il bot e' connesso quando l'applicazione
    # viene aggiunta: il flusso OAuth funziona anche a bot spento e Discord non
    # ritrasmette gli eventi del gateway. All'avvio non ci si fida di aver visto
    # gli eventi, si guarda lo stato — lo stesso pattern della riconciliazione
    # vocale.
    calls, _ = fake_db
    prima = FakeGuild(111, [fake_member(1)])
    seconda = FakeGuild(222, [fake_member(2)])
    bot = SimpleNamespace(guilds=[prima, seconda])

    asyncio.run(backfill.reconcile_guilds(bot))

    assert calls["registered"] == [111, 222]
    assert calls["marked"] == [111, 222]


def test_una_guild_che_fallisce_non_blocca_le_altre(fake_db, monkeypatch):
    calls, _ = fake_db

    async def esplode(*, guild_id, first_seen_at):
        if guild_id == 111:
            raise RuntimeError("API Discord non raggiungibile")
        calls["registered"].append(guild_id)
        return db.GuildRecord(
            guild_id=guild_id, first_seen_at=T0, backfilled_at=None
        )

    monkeypatch.setattr(db, "register_guild", esplode)
    bot = SimpleNamespace(
        guilds=[FakeGuild(111, [fake_member(1)]), FakeGuild(222, [fake_member(2)])]
    )

    asyncio.run(backfill.reconcile_guilds(bot))

    assert calls["registered"] == [222]
    assert calls["marked"] == [222]
