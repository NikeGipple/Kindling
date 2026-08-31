"""Decadimento del legame (modello-grafo.md 5)."""

from __future__ import annotations

from datetime import timedelta

from job.config import DEFAULT_PARAMS
from job.decay import decay_factor


def test_interazione_appena_avvenuta_pesa_uno():
    assert decay_factor(timedelta(0), DEFAULT_PARAMS) == 1.0


def test_al_cutoff_pesa_esattamente_zero():
    # Non "quasi zero": la normalizzazione esiste apposta per evitare il
    # gradino artificiale di un'esponenziale semplice troncata a C.
    assert decay_factor(DEFAULT_PARAMS.decay_cutoff, DEFAULT_PARAMS) == 0.0
    assert decay_factor(timedelta(days=31), DEFAULT_PARAMS) == 0.0


def test_appena_prima_del_cutoff_e_gia_quasi_zero():
    just_before = decay_factor(timedelta(days=30) - timedelta(seconds=1), DEFAULT_PARAMS)
    assert 0.0 < just_before < 1e-5


def test_monotona_decrescente():
    previous = decay_factor(timedelta(0), DEFAULT_PARAMS)
    for hours in range(1, 30 * 24 + 1):
        current = decay_factor(timedelta(hours=hours), DEFAULT_PARAMS)
        assert current < previous, f"non decrescente a {hours}h"
        previous = current


def test_allemivita_vale_circa_meta_del_valore_grezzo():
    # 2^-1 = 0.5 riscalato sull'intervallo [f(C), 1]: leggermente meno di 0.5.
    half_life = decay_factor(DEFAULT_PARAMS.decay_half_life, DEFAULT_PARAMS)
    assert 0.45 < half_life < 0.5


def test_eta_negativa_non_supera_il_contributo_pieno():
    assert decay_factor(timedelta(hours=-1), DEFAULT_PARAMS) == 1.0
