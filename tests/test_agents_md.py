"""``AGENTS.md`` e' un rimando a ``CLAUDE.md``, e deve restare tale.

Il 24/09/2026 nel repository e' comparso un `AGENTS.md` non tracciato: una copia
letterale di `CLAUDE.md`, 483 righe, con «Claude Code» sostituito da «Codex» —
sostituzione che aveva gia' corrotto una frase («fatto da Codex, non da Codex»,
dove l'originale distingueva Claude da Claude Code). Origine ignota: nessun
commit lo nominava, niente nel repository lo legge o lo genera.

**Il problema non era la frase corrotta, era la copia.** Due file con le stesse
regole divergono alla prima modifica di uno dei due, e non fallisce niente: e'
la classe di difetto di `CLAUDE.md` §7 applicata al file che la descrive. Da qui
il rimando, e da qui questo test — perche' se qualcosa lo rigenera come copia,
deve fallire la suite invece di passare in silenzio.

Tre condizioni, e la terza e' quella che riconosce davvero una copia: una
rigenerazione con un `AGENTS.md` piu' corto della soglia ma fatto di pezzi di
`CLAUDE.md` passerebbe le prime due. Le intestazioni si leggono da `CLAUDE.md`,
non si ricopiano qui: cambiarle la' non deve far fallire questo test per la
ragione sbagliata.
"""

from __future__ import annotations

import re
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
AGENTS = RADICE / "AGENTS.md"
CLAUDE = RADICE / "CLAUDE.md"

# Una soglia larga: il rimando di oggi sta in poco piu' di mille byte, e la copia
# che questo test esiste per impedire ne aveva quasi trentamila. Larga apposta —
# qui non si misura lo stile, si distingue un rimando da un manuale.
MASSIMO_BYTE = 4096


def test_agents_md_esiste_ed_e_corto():
    assert AGENTS.is_file(), "AGENTS.md non c'e' piu': serve agli harness che lo cercano"
    dimensione = AGENTS.stat().st_size

    assert dimensione <= MASSIMO_BYTE, (
        f"AGENTS.md e' di {dimensione} byte: non e' piu' un rimando. "
        "Le istruzioni stanno in CLAUDE.md, e due copie divergono in silenzio."
    )
    # Il controllo non deve passare perche' il file e' vuoto: un rimando che non
    # rimanda e' inutile quanto una copia.
    assert dimensione > 200


def test_agents_md_manda_a_claude_md():
    testo = AGENTS.read_text(encoding="utf-8")

    assert "CLAUDE.md" in testo
    # E il file a cui manda esiste davvero: un rimando a un file cancellato e'
    # un rimando rotto, e nessuno se ne accorgerebbe leggendo AGENTS.md.
    assert CLAUDE.is_file()


def test_agents_md_non_e_una_copia_di_claude_md():
    """Il controllo che riconosce una rigenerazione, anche parziale.

    Le intestazioni di `CLAUDE.md` si leggono da `CLAUDE.md`: se domani ne
    cambia una, questo test continua a guardare quelle vere invece di una lista
    ricopiata qui che avrebbe smesso di corrispondere.
    """
    intestazioni = [
        riga.strip()
        for riga in CLAUDE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^#{2,3} \S", riga)
    ]
    assert len(intestazioni) > 5, "CLAUDE.md non ha piu' intestazioni: test da rifare"

    testo = AGENTS.read_text(encoding="utf-8")
    ricopiate = [i for i in intestazioni if i.lstrip("# ") in testo]
    assert ricopiate == [], f"AGENTS.md ricopia sezioni di CLAUDE.md: {ricopiate}"
