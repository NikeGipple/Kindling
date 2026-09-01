"""Ingestion: la transizione di stato vocale produce gli eventi giusti.

Due casi contano qui. Lo spostamento tra canali, che prima non produceva
nessun evento e lasciava chi si spostava aperto per sempre nel canale
sbagliato. E la riconciliazione all'avvio, che deve scrivere nel marcatore di
riavvio le presenze che ha trovato ancora in corso: e' l'unico modo in cui
"questa sessione non si e' interrotta" diventa un dato leggibile dal job
invece di un non-evento.

Nessun database e nessun gateway Discord: i listener sono chiamati a mano con
oggetti finti che espongono solo cio' che il cog legge davvero.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot import event_types
from bot.cogs import ingestion
from job.db import _parse_voice_confirmed


class FakeChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FakeChannel) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


def fake_member(member_id: int = 1, *, bot: bool = False):
    return SimpleNamespace(id=member_id, bot=bot, guild=SimpleNamespace(id=111))


def state(channel: object | None):
    return SimpleNamespace(channel=channel)


@pytest.fixture()
def written(monkeypatch):
    """Cattura le chiamate a insert_raw_event invece di scriverle."""
    calls: list[dict] = []

    async def fake_insert(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(ingestion.db, "insert_raw_event", fake_insert)
    return calls


def run_voice_update(before, after, member=None):
    cog = ingestion.IngestionCog(bot=None)
    asyncio.run(
        cog.on_voice_state_update(member or fake_member(), state(before), state(after))
    )


def test_ingresso_produce_un_solo_join(written):
    run_voice_update(None, FakeChannel(900, "generale"))

    assert [c["event_type"] for c in written] == [event_types.VOICE_JOIN]
    assert written[0]["channel_id"] == 900


def test_uscita_produce_un_solo_leave(written):
    run_voice_update(FakeChannel(900, "generale"), None)

    assert [c["event_type"] for c in written] == [event_types.VOICE_LEAVE]
    assert written[0]["channel_id"] == 900


def test_spostamento_produce_leave_e_join_con_lo_stesso_timestamp(written):
    run_voice_update(FakeChannel(900, "generale"), FakeChannel(901, "musica"))

    assert [c["event_type"] for c in written] == [
        event_types.VOICE_LEAVE,
        event_types.VOICE_JOIN,
    ]
    assert [c["channel_id"] for c in written] == [900, 901]
    # Stesso istante: se differissero, la ricostruzione degli intervalli
    # vedrebbe tra i due canali un buco che nella realta' non esiste.
    assert written[0]["occurred_at"] == written[1]["occurred_at"]


def test_mute_e_deafen_non_producono_eventi(written):
    canale = FakeChannel(900, "generale")
    run_voice_update(canale, canale)

    assert written == []


def test_i_bot_sono_esclusi(written):
    run_voice_update(None, FakeChannel(900, "generale"), member=fake_member(2, bot=True))

    assert written == []


def fake_guild(*, occupants: dict[int, FakeChannel]):
    """Guild finta con i suoi canali vocali gia' popolati."""
    channels: dict[int, object] = {}
    for member_id, channel in occupants.items():
        entry = channels.setdefault(
            channel.id, SimpleNamespace(id=channel.id, name=channel.name, members=[])
        )
        entry.members.append(fake_member(member_id))
    return SimpleNamespace(
        id=111,
        voice_channels=list(channels.values()),
        stage_channels=[],
        get_channel=lambda channel_id: channels.get(channel_id),
    )


@pytest.fixture()
def reconciliation(monkeypatch):
    """Stato del DB visto dalla riconciliazione, senza un DB."""

    def configure(*, open_sessions, last_event):
        async def fake_last_event_at(**kwargs):
            return last_event

        async def fake_open_sessions(**kwargs):
            return open_sessions

        monkeypatch.setattr(ingestion.db, "last_event_at", fake_last_event_at)
        monkeypatch.setattr(ingestion.db, "fetch_open_voice_sessions", fake_open_sessions)

    return configure


def test_la_presenza_confermata_finisce_nel_marcatore(written, reconciliation):
    # Ada e' in vocale da un'ora e ci e' ancora al riavvio: la sua sessione
    # resta aperta, ma "restare aperta" e' un non-evento. Senza l'elenco delle
    # presenze confermate nel marcatore, il job non puo' distinguerla da un
    # leave perso nel downtime e la chiuderebbe qui.
    canale = FakeChannel(900, "generale")
    joined_at = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
    reconciliation(
        open_sessions=[(1, 900, joined_at)],
        last_event=datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc),
    )

    cog = ingestion.IngestionCog(bot=None)
    asyncio.run(cog._reconcile_voice_state(fake_guild(occupants={1: canale})))

    # Un solo evento scritto: il marcatore. Nessun leave ricostruito, nessun
    # join sintetico per chi era gia' allineato.
    assert [c["event_type"] for c in written] == [event_types.BOT_RESTART]
    marker = written[0]["payload"]
    assert marker[event_types.VOICE_CONFIRMED_KEY] == [
        {"author_id": 1, "channel_id": 900}
    ]
    # Il downtime resta l'ultimo evento noto: il marcatore va scritto dopo
    # aver letto last_event_at, altrimenti l'ultimo evento e' se stesso.
    assert marker["downtime_start"] == "2026-08-03T21:00:00+00:00"


def test_il_job_rilegge_le_conferme_scritte_dal_bot(written, reconciliation):
    # I due capi del contratto, verificati insieme: il payload che il cog
    # scrive e' esattamente quello che il job sa rileggere. Sono due moduli
    # che non si importano a vicenda di proposito (il job non dipende da
    # discord.py), quindi niente li tiene allineati se non un test.
    canale = FakeChannel(900, "generale")
    reconciliation(
        open_sessions=[(1, 900, datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc))],
        last_event=datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc),
    )

    cog = ingestion.IngestionCog(bot=None)
    asyncio.run(cog._reconcile_voice_state(fake_guild(occupants={1: canale})))

    # json.dumps/loads e' il giro che fa il payload passando da JSONB.
    stored = json.dumps(written[0]["payload"][event_types.VOICE_CONFIRMED_KEY])

    assert _parse_voice_confirmed(stored) == frozenset({(1, 900)})


def test_il_job_tratta_il_marcatore_vecchio_come_senza_conferme():
    # Marcatore gia' in produzione, scritto prima che il campo esistesse:
    # chiave assente, nessuna conferma, comportamento di prima. Nessuna
    # migrazione dei dati esistenti.
    assert _parse_voice_confirmed(None) == frozenset()


def test_una_sessione_non_piu_in_canale_non_viene_confermata(written, reconciliation):
    # Bo risultava in #generale ma al riavvio non c'e' piu': il leave e'
    # andato perso e va ricostruito, non confermato.
    joined_at = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
    reconciliation(
        open_sessions=[(2, 900, joined_at)],
        last_event=datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc),
    )

    cog = ingestion.IngestionCog(bot=None)
    asyncio.run(cog._reconcile_voice_state(fake_guild(occupants={})))

    assert [c["event_type"] for c in written] == [
        event_types.BOT_RESTART,
        event_types.VOICE_LEAVE,
    ]
    assert written[0]["payload"][event_types.VOICE_CONFIRMED_KEY] == []
    # Chiuso all'ultimo istante in cui il bot era vivo, non all'ora del
    # riavvio: chiudere piu' tardi gonfierebbe la sessione del downtime.
    assert written[1]["occurred_at"] == datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc)


def test_chi_e_in_canale_senza_sessione_aperta_riceve_un_join_sintetico(
    written, reconciliation
):
    canale = FakeChannel(900, "generale")
    reconciliation(open_sessions=[], last_event=None)

    cog = ingestion.IngestionCog(bot=None)
    asyncio.run(cog._reconcile_voice_state(fake_guild(occupants={3: canale})))

    assert [c["event_type"] for c in written] == [
        event_types.BOT_RESTART,
        event_types.VOICE_JOIN,
    ]
    assert written[0]["payload"][event_types.VOICE_CONFIRMED_KEY] == []
    assert written[1]["payload"][event_types.RECONSTRUCTED_KEY] is True


def test_eventi_ricostruiti_sono_marcati_nel_payload(written):
    cog = ingestion.IngestionCog(bot=None)
    asyncio.run(
        cog._emit_voice_event(
            guild_id=111,
            author_id=1,
            channel_id=900,
            channel_name="generale",
            event_type=event_types.VOICE_LEAVE,
            occurred_at=datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc),
            reconstruction_reason=event_types.REASON_BOT_RESTART,
        )
    )

    payload = written[0]["payload"]
    assert payload[event_types.RECONSTRUCTED_KEY] is True
    assert payload[event_types.RECONSTRUCTION_REASON_KEY] == event_types.REASON_BOT_RESTART
    # Idempotente: un secondo riavvio con lo stesso istante non duplica.
    assert written[0]["dedup_key"] is not None
