// Unico comportamento JS: conferma prima delle azioni delicate (data-confirm).
// Senza JS i moduli funzionano comunque; la conferma e' un'ulteriore cautela.
document.addEventListener("click", function (event) {
  var el = event.target.closest("[data-confirm]");
  if (el && !window.confirm(el.getAttribute("data-confirm"))) {
    event.preventDefault();
  }
});
