"""Job di calcolo del grafo sociale di Kindling.

Legge ``raw_events`` da Postgres, ricostruisce le sessioni vocali e gli archi
dei quattro layer (voice, reply, mention, reaction) e scrive uno snapshot in
``graph_snapshots``/``graph_edges``. Si ferma agli archi: nessuna metrica SNA,
nessun endpoint.

Non chiama mai l'API Discord e non tiene stato: il grafo in memoria e'
transiente, si ricalcola sempre da ``raw_events``. Vedi
docs/architettura/modello-grafo.md per il modello e i parametri.
"""
