"""Decadimento del legame nel tempo (modello-grafo.md 5).

Un legame non ha un peso assoluto: ha un peso *rispetto a un istante*. Il peso
di un arco allo snapshot e' la somma dei contributi delle singole interazioni,
ciascuna pesata per la propria eta' rispetto ad ``as_of``.
"""

from __future__ import annotations

from datetime import timedelta

from .config import GraphParams


def decay_factor(age: timedelta, params: GraphParams) -> float:
    """Fattore di decadimento ``f`` per un'interazione vecchia di ``age``.

        f(d) = (2^(-d/H) - 2^(-C/H)) / (1 - 2^(-C/H))   per 0 <= d < C
        f(d) = 0                                        per d >= C

    La normalizzazione fa si' che ``f(0) = 1`` e ``f(C) = 0`` esattamente: la
    curva scende con continuita' fino a zero al cutoff, senza il gradino
    artificiale che avrebbe un'esponenziale semplice troncata a C.

    Un'eta' negativa (interazione successiva ad ``as_of``) non dovrebbe mai
    arrivare fin qui, perche' la finestra si chiude su ``as_of``; se arriva,
    vale 1 invece di superarlo: un peso maggiore del contributo pieno non ha
    senso in nessuna lettura.
    """
    cutoff = params.decay_cutoff
    if age >= cutoff:
        return 0.0
    if age <= timedelta(0):
        return 1.0

    half_life_seconds = params.decay_half_life.total_seconds()
    at_cutoff = 2.0 ** (-cutoff.total_seconds() / half_life_seconds)
    raw = 2.0 ** (-age.total_seconds() / half_life_seconds)
    return (raw - at_cutoff) / (1.0 - at_cutoff)
