# Note per CLAUDE.md — da riportare nel repo

*Estratto pronto da incollare in `CLAUDE.md` (il file vive nel repository, non
nel progetto Claude — vedi `lezioni-docker-compose.md`). Raccoglie
due chiarimenti emersi durante la verifica dell'estratto `raw_events` del
28-29/08/2026: un gap di schema ancora aperto e una clarificazione
terminologica da fissare prima che si insinui ambiguità nel codice o nei
prompt futuri.*

> **Aggiornamento 30/08/2026 — il gap sulla tabella `members` è chiuso.**
> Verificato leggendo direttamente il codice del repo (`migrations/0002_members.sql`,
> `bot/db.py`, `bot/cogs/admin.py`, `bot/cogs/ingestion.py`): la tabella esiste,
> è popolata in tempo reale da `on_member_join`/`on_member_remove`, ed è
> disponibile il backfill una tantum (`!backfill_members`). Il `CLAUDE.md` nel
> repo è già stato aggiornato di conseguenza ("Implementata... non è più un
> gap"). Il resto di questa nota descrive lo stato *prima* di quella modifica
> ed è mantenuto solo come contesto storico — non riflette più lo stato
> attuale del codice.
>
> In questa stessa verifica è emerso anche che il payload dei messaggi
> (`bot/cogs/ingestion.py`, `on_message`) **non contiene mai** il testo del
> messaggio, deliberatamente: solo `content_length`, `has_attachments`,
> `mention_ids`, `channel_name`. La dicitura dell'informativa privacy
> ("Mai il contenuto testuale del messaggio") è quindi accurata rispetto al
> codice attuale.
>
> Corretta invece l'informativa privacy dove diceva che l'ID Discord raccolto
> è "anonimizzato": nello schema `author_id` è l'ID Discord grezzo (BIGINT),
> usato direttamente per collegare eventi/membri — non è né anonimizzato né
> pseudonimizzato in senso tecnico. Testo corretto per non promettere una
> protezione che il sistema non implementa (vedi `legal/informativa-privacy.html`,
> punto 3 e punto 5 — quest'ultimo aggiornato anche per specificare "server
> nell'Unione Europea" senza nominare il fornitore di hosting).

## 1. Gap aperto — tabella `members` non ancora implementata

```markdown
## Gap noto: tabella `members`

Non esiste ancora (né nel repo né in un doc di schema). Prima di lavorare su
retention/onboarding, va creata:

- Tabella con `author_id` (PK), `guild_id`, `joined_at`, `left_at` (nullable),
  `forgotten_at` (nullable, stessa semantica GDPR già su `raw_events`).
- **Backfill una tantum urgente** via API Discord (l'oggetto membro espone già
  `joined_at`) per tutti i membri già presenti — se qualcuno esce prima del
  backfill, il suo `joined_at` si perde per sempre. Priorità alta, non rimandabile.
- Due nuovi tipi di evento nel bot: `member_join` / `member_remove`
  (`on_member_join` / `on_member_remove` di discord.py), coerenti con la
  filosofia raw-event-come-fonte-di-verità già usata per il resto.
- `left_at` non è opzionale: senza uscite tracciate non si può mai verificare
  se l'integrazione rapida (es. Millington, 5 connessioni distinte) correla
  davvero con la retention — è il termine di paragone mancante.
- Edge case rientro dopo uscita: per l'MVP, sovrascrivere `joined_at`/`left_at`
  (nessuno storico multi-rientro). Scelta esplicita, da rivedere se in futuro
  serve tracciare rientri multipli.
```

## 2. Chiarimento terminologico — `guild_id`

```markdown
## Terminologia: `guild_id`

`guild_id` indica **sempre e solo il server Discord**, mai la gilda in-game
di Guild Wars 2. Sono due entità diverse, con fonti dati diverse:

- `guild_id` → server Discord (fonte: eventi Discord, già presente su
  `raw_events` e nello schema).
- Gilda in-game GW2 → fonte dati separata, non ancora integrata. Se in futuro
  verrà integrata, andrà chiamata esplicitamente `gw2_guild_id` per evitare
  ambiguità con `guild_id`.

Non usare mai "guild" da solo per riferirsi alla gilda GW2 nel codice o nello
schema: nel contesto Discord/discord.py "guild" è già un termine riservato
con un significato preciso (= server).
```

## Contesto: verifica estratto `raw_events` (chiusa)

Analizzato un estratto di 72 eventi (28-29/08/2026). Struttura interna
pulita: nessun buco negli ID, nessuna incoerenza canale/nome, nessun evento
fuori ordine. Copertura temporale parziale confermata normale (il bot è
partito da poco, non manca nulla). Buco notturno e sessioni voice ancora
aperte a fine estratto: normali, non anomalie. È da questa verifica che è
emerso il gap sulla tabella `members` sopra.
