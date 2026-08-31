"""Ingestion: la transizione di stato vocale produce gli eventi giusti.

Il caso che conta e' lo spostamento tra canali: prima non produceva nessun
evento, e chi si spostava restava aperto per sempre nel canale sbagliato.
Nessun database e nessun gateway Discord: i listener sono chiamati a mano con
oggetti finti che espongono solo cio' che il cog legge davvero.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot import event_types
from bot.cogs import ingestion


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
