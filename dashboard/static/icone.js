// Le icone dei server che non arrivano (dashboard.md 4, «L'elenco dei server»).
//
// L'unico script della dashboard, e c'e' per una ragione sola: Chromium disegna
// il glifo dell'immagine rotta anche su un <img alt="">, e il CSS non riesce a
// nasconderlo per bene (provato il 27/09/2026: un ::after sull'immagine rotta
// piu' un clip-path del primo pixel lasciano a 400% un filo sul bordo). Firefox
// non lo disegna. Qui l'<img> che non si carica si toglie, e resta il monogramma
// che gli sta sotto. Senza script la pagina e' identica, glifo a parte.
//
// Niente onerror="..." nel markup: la CSP (senza 'unsafe-inline') lo
// bloccherebbe in silenzio. Caricato con defer, dall'elenco soltanto.
(function () {
  "use strict";

  function icona(elemento) {
    return elemento instanceof HTMLImageElement && elemento.closest(".avatar") !== null;
  }

  function togli(img) {
    if (img.parentNode) {
      img.parentNode.removeChild(img);
    }
  }

  // In cattura: l'evento error di un'immagine non risale fino a document.
  document.addEventListener("error", function (evento) {
    if (icona(evento.target)) {
      togli(evento.target);
    }
  }, true);

  // L'errore puo' essere arrivato prima dello script: le immagini gia' fallite
  // hanno complete vero e nessuna larghezza. Una lazy non ancora caricata ha
  // complete falso, e se fallira' la prende l'ascoltatore qui sopra.
  document.querySelectorAll(".avatar img").forEach(function (img) {
    if (img.complete && img.naturalWidth === 0) {
      togli(img);
    }
  });
})();
