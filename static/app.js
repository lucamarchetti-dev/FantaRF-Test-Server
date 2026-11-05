// Unico comportamento JS: conferma prima delle azioni delicate (data-confirm).
// Senza JS i moduli funzionano comunque; la conferma e' un'ulteriore cautela.
document.addEventListener("click", function (event) {
  var el = event.target.closest("[data-confirm]");
  if (el && !window.confirm(el.getAttribute("data-confirm"))) {
    event.preventDefault();
  }
});

// Modulo «Nuovo account»: il campo Squadra e' obbligatorio solo per i partecipanti (il server controlla comunque).
(function () {
  var role = document.querySelector("[data-team-field]");
  if (!role) { return; }
  var team = document.getElementById(role.getAttribute("data-team-field"));
  if (!team) { return; }
  function sync() {
    team.required = role.value === role.getAttribute("data-team-required-for");
  }
  role.addEventListener("change", sync);
  sync();
})();
