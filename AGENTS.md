# Kindling — istruzioni per gli agenti

**Le istruzioni per qualunque agente che lavori su questo repository stanno in
[`CLAUDE.md`](CLAUDE.md), e vanno lette per intero prima di toccare qualcosa.**
Non c'è una versione breve: quel file è fatto di decisioni motivate e di errori
già commessi qui dentro, e un riassunto perderebbe proprio le motivazioni, che
sono la parte che serve.

Questo file esiste solo perché alcuni harness cercano `AGENTS.md` e non
`CLAUDE.md`. È un rimando, e deve restare tale.

**Non deve contenere istruzioni proprie.** Fino al 25/09/2026 ne conteneva: era
una copia letterale di `CLAUDE.md`, 483 righe, con «Claude Code» sostituito da
«Codex» — e una di quelle sostituzioni aveva già corrotto una frase, riducendo
«fatto da Claude, non da Claude Code» a «fatto da Codex, non da Codex», che non
distingue più niente. Due copie delle stesse regole divergono alla prima
modifica di una delle due, e **nessun controllo fallisce**: è esattamente la
classe di difetto descritta in `CLAUDE.md` §7, applicata al file che la descrive.

`tests/test_agents_md.py` fallisce se questo file torna a essere lungo o smette
di nominare `CLAUDE.md`.
