-- 0012_schema_migrations.sql
--
-- Il ledger di quali migration sono applicate su QUESTO database. Additiva e
-- idempotente come le precedenti.
--
-- Il problema che risolve non e' astratto: "quali migration sono applicate
-- sulla droplet" oggi e' un fatto sul database custodito FUORI dal database —
-- scritto a mano altrove (stato del progetto, memoria di chi ha fatto il
-- deploy). Puo' divergere dalla realta' senza che niente lo segnali, ed e' la
-- stessa forma dei difetti che questo stesso branch corregge altrove (CLAUDE.md
-- 7): un meccanismo che sembra tenere traccia e in realta' non lo fa in modo
-- verificabile.
--
-- ops/kindling-deploy.sh confronta i file in migrations/ con le righe di questa
-- tabella per fermarsi se qualcosa non e' stato applicato — vedi quello script
-- e runbook-droplet.md per come viene usata.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE schema_migrations IS
    'Ledger delle migration applicate su questo database. Popolato dalle migration stesse: ognuna, a partire da questa, finisce registrando il proprio nome file. NON leggibile da kindling_api (migration 0010 non le da'' il GRANT): e'' stato interno del deploy, non un aggregato da servire.';

-- Nessuna colonna checksum dell'impronta del file. Ci si potrebbe essere
-- tentati: registrare un hash segnalerebbe "il file e' stato modificato dopo
-- essere stato applicato". Ma in questo repo i commenti si riscrivono spesso
-- — e' successo proprio a 0011 lo stesso giorno in cui e' stata scritta,
-- senza che il contenuto eseguibile cambiasse — e un controllo che confronta
-- l'hash produrrebbe quasi solo falsi allarmi. Un allarme che grida sempre
-- insegna a ignorarlo, che e' peggio di nessun allarme.

-- ---------------------------------------------------------------------------
-- Backfill: le undici migration gia' esistenti
-- ---------------------------------------------------------------------------
--
-- Questa e' un'ASSERZIONE, non un'osservazione: dichiara che 0001-0011 sono
-- gia' state applicate su questo database. Verificabile su questa droplet — lo
-- schema contiene gia' tutto cio' che 0001-0011 creano, per costruzione,
-- altrimenti nessuna delle migration successive avrebbe potuto applicarsi
-- pulita — ma resta un'affermazione fatta al momento in cui questo file e'
-- stato scritto (07/09/2026), non qualcosa che questa migration controlla da
-- sola. Se in futuro dovesse esistere un database su cui QUESTO backfill e'
-- falso (uno scenario non previsto oggi), applicare 0012 li' registrerebbe
-- undici migration come fatte senza averle verificate: e' il limite dichiarato
-- di un backfill, non un difetto nascosto.
INSERT INTO schema_migrations (filename) VALUES
    ('0001_raw_events.sql'),
    ('0002_members.sql'),
    ('0003_referenced_channel_id.sql'),
    ('0004_graph_snapshots.sql'),
    ('0005_snapshot_stats.sql'),
    ('0006_metrics.sql'),
    ('0007_metrics_observability.sql'),
    ('0008_guilds.sql'),
    ('0009_guild_rejoined_at.sql'),
    ('0010_api_role.sql')
ON CONFLICT (filename) DO NOTHING;

-- 0011 e' un caso a parte nel backfill: e' la migration che ha reso necessario
-- questo ledger (il deploy del 07/09/2026 in cui si e' scoperto che "quali
-- migration sono applicate" non si poteva verificare), quindi va registrata
-- come le altre dieci, con la stessa assunzione dichiarata sopra.
INSERT INTO schema_migrations (filename) VALUES ('0011_previous_gap_days.sql')
    ON CONFLICT (filename) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Autoregistrazione: la convenzione comincia da questo file stesso
-- ---------------------------------------------------------------------------
--
-- Da qui in avanti OGNI migration finisce con una riga cosi', con il proprio
-- nome file. Non un passo separato per l'operatore: un ledger che qualcuno deve
-- ricordarsi di aggiornare a mano e' peggio di nessun ledger, perche' quando
-- diverge lo fa in silenzio e sembra affidabile. La riga sta nel file, quindi
-- non puo' mancare se il file e' stato applicato — e non puo' esserci se non
-- lo e' stato. Una migration senza questa riga e' incompleta (CLAUDE.md).
INSERT INTO schema_migrations (filename) VALUES ('0012_schema_migrations.sql')
    ON CONFLICT (filename) DO NOTHING;
