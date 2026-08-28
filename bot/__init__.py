"""Bot Discord di ingestion per Kindling.

Cattura eventi grezzi (messaggi, reply, reazioni, thread, voice, RSVP) e li
scrive, senza mai interpretarli, nella tabella append-only ``raw_events``.
Il calcolo del grafo sociale e delle metriche avviene altrove, a batch,
leggendo da li'. Vedi architettura/stack-tecnologico-mvp.md.
"""
