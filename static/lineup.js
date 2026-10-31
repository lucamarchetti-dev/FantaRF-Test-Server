// Selettore della formazione. Aiuta l'utente (blocca le scelte che violerebbero le regole e
// spiega perché), ma NON e' l'autorita': il server ripete tutti i controlli a ogni invio.
(function () {
  "use strict";
  var dataEl = document.getElementById("builder-data");
  if (!dataEl) { return; }

  var data = JSON.parse(dataEl.textContent);
  var SIZE = 6, PER_SERIE = 3, PAGE = 40;
  var ROLES = ["P", "D", "T", "C", "W", "A"];
  var ROLE_ORDER = {}; ROLES.forEach(function (r, i) { ROLE_ORDER[r] = i; });
  var onePerRole = !!data.onePerRole;

  function norm(s) { return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }
  var byId = {};
  data.players.forEach(function (p) { p.k = norm(p.n); byId[p.id] = p; });

  var selected = data.selected.filter(function (id) { return byId[id]; });
  var shown = PAGE;

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    slots: $("slots"), rules: $("rules"), hidden: $("hidden-ids"), results: $("results"), count: $("pcount"),
    more: $("more"), q: $("pq"), s: $("ps"), r: $("pr"), t: $("pt"), submit: $("submit"), barcount: $("barcount"),
    barhint: $("barhint"), announce: $("announce"), form: $("lineup-form")
  };

  function make(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined) { n.textContent = text; }
    return n;
  }
  function roleBadge(r) {
    var b = make("span", "role role-" + r);
    var l = make("span", null, r); l.setAttribute("aria-hidden", "true");
    b.appendChild(l); b.appendChild(make("span", "sr-only", data.roles[r]));
    b.title = data.roles[r];
    return b;
  }
  function serieBadge(s) {
    var b = make("span", "serie"); b.appendChild(make("span", "sr-only", "Serie ")); b.appendChild(document.createTextNode(s));
    return b;
  }
  // Stemma della squadra: l'immagine se il file esiste (data.crests), altrimenti lo spazio vuoto tratteggiato.
  function crest(team) {
    var url = data.crests && data.crests[team];
    if (url) {
      var img = make("img", "crest");
      img.src = url; img.alt = ""; img.width = 24; img.height = 24; img.loading = "lazy"; img.decoding = "async";
      return img;
    }
    var empty = make("span", "crest crest-empty");
    empty.setAttribute("aria-hidden", "true");
    return empty;
  }
  function teamLabel(team, serie) {
    var l = make("span", "team-label");
    l.appendChild(crest(team)); l.appendChild(make("span", "team-name", team)); l.appendChild(serieBadge(serie));
    return l;
  }
  // Nome del giocatore come collegamento alla sua scheda (media e storico voti). Si apre in una nuova scheda
  // del browser, cosi' la formazione che si sta componendo non va persa.
  function playerName(p) {
    var strong = make("strong");
    if (!data.playerUrl) { strong.textContent = p.n; return strong; }
    var a = make("a", "pname-link", p.n);
    a.href = data.playerUrl.replace(/0$/, String(p.id));
    a.target = "_blank"; a.rel = "noopener";
    a.appendChild(make("span", "sr-only", " (storico voti, si apre in una nuova scheda)"));
    strong.appendChild(a);
    return strong;
  }
  // Media dei voti ufficiali. Senza voti pubblicati: «—» (e lo dice anche a voce), mai 0.
  function average(p) {
    var box = make("span", "avg" + (p.m ? "" : " avg-none"));
    box.appendChild(make("span", "avg-label", "Media"));
    if (p.m) {
      box.appendChild(make("strong", "avg-num", p.m));
      box.appendChild(make("span", "avg-n", p.c + (p.c === 1 ? " voto" : " voti")));
    } else {
      var dash = make("span", "avg-num", "\u2014"); dash.setAttribute("aria-hidden", "true");
      box.appendChild(dash);
      box.appendChild(make("span", "sr-only", "nessun voto pubblicato"));
    }
    return box;
  }
  function sortSel(list) {
    return list.slice().sort(function (a, b) {
      return (ROLE_ORDER[a.r] - ROLE_ORDER[b.r]) || a.k.localeCompare(b.k);
    });
  }
  function chosen() { return selected.map(function (id) { return byId[id]; }); }

  // ---------- analisi della selezione (specchio delle regole del server)
  function analyse() {
    var ch = chosen(), a = { n: ch.length, A: 0, B: 0, teams: {}, roles: {} };
    ch.forEach(function (p) {
      a[p.s] += 1;
      (a.teams[p.t.toLowerCase()] = a.teams[p.t.toLowerCase()] || []).push(p);
      (a.roles[p.r] = a.roles[p.r] || []).push(p);
    });
    a.sameTeam = Object.keys(a.teams).map(function (k) { return a.teams[k]; }).filter(function (g) { return g.length > 1; });
    a.dupRoles = Object.keys(a.roles).map(function (k) { return a.roles[k]; }).filter(function (g) { return g.length > 1; });
    var problems = [];
    if (a.n < SIZE) { problems.push("Mancano " + (SIZE - a.n) + " giocatori"); }
    if (a.n > SIZE) { problems.push("Togli " + (a.n - SIZE) + " giocatori"); }
    if (a.A > PER_SERIE) { problems.push("Troppi giocatori di Serie A"); }
    if (a.B > PER_SERIE) { problems.push("Troppi giocatori di Serie B"); }
    if (a.sameTeam.length) { problems.push("Due giocatori della stessa squadra"); }
    if (onePerRole && a.dupRoles.length) { problems.push("Un ruolo è ripetuto"); }
    a.problems = problems;
    a.ok = problems.length === 0;
    return a;
  }

  // ---------- perché un giocatore non si può aggiungere
  function blockReason(p, a) {
    if (a.n >= SIZE) { return "Formazione già completa"; }
    if (a[p.s] >= PER_SERIE) { return "Hai già " + PER_SERIE + " giocatori di Serie " + p.s; }
    var mates = a.teams[p.t.toLowerCase()];
    if (mates) { return "Hai già scelto " + mates[0].n + " (" + p.t + ")"; }
    if (onePerRole && a.roles[p.r]) { return "Ruolo " + p.r + " già coperto da " + a.roles[p.r][0].n; }
    return null;
  }

  // ---------- disegno
  function renderSlots(a) {
    el.slots.textContent = "";
    var sel = sortSel(chosen());
    function filled(p) {
      var li = make("li", "slot slot-filled band band-" + p.r);
      li.appendChild(roleBadge(p.r));
      var info = make("span", "slot-info");
      info.appendChild(make("strong", null, p.n));
      var sub = make("span", "slot-sub"); sub.appendChild(teamLabel(p.t, p.s));
      info.appendChild(sub); li.appendChild(info);
      var rm = make("button", "btn btn-quiet btn-sm", "Rimuovi");
      rm.type = "button"; rm.setAttribute("data-remove", p.id); rm.setAttribute("aria-label", "Rimuovi " + p.n);
      li.appendChild(rm);
      return li;
    }
    function empty(label, role) {
      var li = make("li", "slot slot-empty");
      li.appendChild(make("span", "slot-info", label));
      if (role) {
        var f = make("button", "btn btn-quiet btn-sm", "Cerca");
        f.type = "button"; f.setAttribute("data-find-role", role); f.setAttribute("aria-label", "Cerca un giocatore con ruolo " + role);
        li.appendChild(f);
      }
      return li;
    }
    if (onePerRole) {
      ROLES.forEach(function (r) {
        var here = sel.filter(function (p) { return p.r === r; });
        if (here.length) { here.forEach(function (p) { el.slots.appendChild(filled(p)); }); }
        else { el.slots.appendChild(empty(r + " – " + data.roles[r] + ": da scegliere", r)); }
      });
    } else {
      sel.forEach(function (p) { el.slots.appendChild(filled(p)); });
      for (var i = sel.length; i < SIZE; i++) { el.slots.appendChild(empty("Posto libero")); }
    }
  }

  function renderRules(a) {
    el.rules.textContent = "";
    function item(state, text) {
      var li = make("li", "rule rule-" + state);
      var mark = { ok: "✓", bad: "✗", todo: "○", warn: "!" }[state];
      var m = make("span", "mark", mark); m.setAttribute("aria-hidden", "true");
      li.appendChild(m); li.appendChild(make("span", null, text));
      li.appendChild(make("span", "sr-only", { ok: " (rispettata)", bad: " (non rispettata)", todo: " (da completare)", warn: " (avviso)" }[state]));
      el.rules.appendChild(li);
    }
    item(a.n === SIZE ? "ok" : (a.n > SIZE ? "bad" : "todo"), "Giocatori scelti: " + a.n + " su " + SIZE);
    ["A", "B"].forEach(function (s) {
      item(a[s] === PER_SERIE ? "ok" : (a[s] > PER_SERIE ? "bad" : "todo"), "Serie " + s + ": " + a[s] + " su " + PER_SERIE);
    });
    if (a.sameTeam.length) {
      a.sameTeam.forEach(function (g) {
        item("bad", g.map(function (p) { return p.n; }).join(" e ") + " giocano nella stessa squadra (" + g[0].t + ")");
      });
    } else {
      item(a.n === 0 ? "todo" : "ok", "Squadre tutte diverse");
    }
    if (onePerRole) {
      var covered = ROLES.filter(function (r) { return a.roles[r]; }).length;
      item(a.dupRoles.length ? "bad" : (covered === SIZE ? "ok" : "todo"),
        "Un giocatore per ruolo: " + covered + " ruoli su " + SIZE + " coperti" + (a.dupRoles.length ? " (ruolo ripetuto)" : ""));
    } else if (a.dupRoles.length) {
      item("warn", "Hai più giocatori con lo stesso ruolo. Nell'esempio del regolamento ogni ruolo compare una sola volta: chiedi allo staff se qui è richiesto.");
    }
  }

  function filtered() {
    var q = norm(el.q.value.trim()), s = el.s.value, r = el.r.value, t = el.t.value;
    return data.players.filter(function (p) {
      return (!q || p.k.indexOf(q) !== -1) && (!s || p.s === s) && (!r || p.r === r) && (!t || p.t === t);
    });
  }

  function renderResults(a, focusId) {
    var list = filtered(), part = list.slice(0, shown);
    el.results.textContent = "";
    part.forEach(function (p) {
      var isSel = selected.indexOf(p.id) !== -1;
      var reason = isSel ? null : blockReason(p, a);
      var li = make("li", "result band band-" + p.r + (isSel ? " is-selected" : "") + (reason ? " is-blocked" : ""));
      li.setAttribute("data-id", p.id);
      li.appendChild(roleBadge(p.r));
      var info = make("span", "slot-info");
      info.appendChild(playerName(p));
      var sub = make("span", "slot-sub"); sub.appendChild(teamLabel(p.t, p.s));
      info.appendChild(sub);
      if (reason) { info.appendChild(make("span", "why", reason)); }
      li.appendChild(info);
      li.appendChild(average(p));
      if (reason) {
        // Riga non selezionabile: niente pulsante, il motivo e' scritto in chiaro sotto il nome.
        li.setAttribute("aria-disabled", "true");
        info.insertBefore(make("span", "sr-only", "Non disponibile. "), info.firstChild);
      } else {
        var b = make("button", "btn btn-sm " + (isSel ? "btn-quiet" : ""), isSel ? "Rimuovi" : "Aggiungi");
        b.type = "button";
        if (isSel) { b.setAttribute("data-remove", p.id); b.setAttribute("aria-label", "Rimuovi " + p.n); b.setAttribute("aria-pressed", "true"); }
        else { b.setAttribute("data-add", p.id); b.setAttribute("aria-label", "Aggiungi " + p.n); }
        li.appendChild(b);
      }
      el.results.appendChild(li);
    });
    var total = list.length;
    el.count.textContent = total === 0 ? "Nessun giocatore trovato: prova a cambiare i filtri."
      : (total === 1 ? "1 giocatore trovato." : total + " giocatori trovati" + (total > part.length ? " (mostrati i primi " + part.length + ")" : "") + ".");
    el.more.hidden = total <= part.length;
    if (focusId) {
      var again = el.results.querySelector('[data-id="' + focusId + '"] button');
      if (again) { again.focus(); }
    }
  }

  function renderBar(a) {
    el.barcount.textContent = a.n + " su " + SIZE;
    el.submit.disabled = !a.ok;
    el.barhint.textContent = a.ok ? "Pronta da inviare" : a.problems[0];
    el.hidden.textContent = "";
    selected.forEach(function (id) {
      var i = document.createElement("input"); i.type = "hidden"; i.name = "player_id"; i.value = id; el.hidden.appendChild(i);
    });
  }

  function render(focusId) {
    var a = analyse();
    renderSlots(a); renderRules(a); renderResults(a, focusId); renderBar(a);
    return a;
  }

  function say(text, a) { el.announce.textContent = text + " " + a.n + " giocatori su " + SIZE + "."; }

  // ---------- eventi
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("button");
    if (!t) { return; }
    var add = t.getAttribute("data-add"), rem = t.getAttribute("data-remove"), find = t.getAttribute("data-find-role");
    if (add) {
      var id = parseInt(add, 10);
      if (selected.indexOf(id) === -1 && !blockReason(byId[id], analyse())) {
        selected.push(id);
        var a = render(id); say("Aggiunto " + byId[id].n + ".", a);
      }
    } else if (rem) {
      var rid = parseInt(rem, 10);
      selected = selected.filter(function (x) { return x !== rid; });
      var a2 = render(rid); say("Rimosso " + byId[rid].n + ".", a2);
    } else if (find) {
      el.r.value = find; shown = PAGE; render(); el.q.focus();
    }
  });
  el.more.addEventListener("click", function () { shown += PAGE; render(); });
  [el.q, el.s, el.r, el.t].forEach(function (c) {
    c.addEventListener("input", function () { shown = PAGE; renderResults(analyse()); });
  });
  el.form.addEventListener("submit", function (ev) { if (!analyse().ok) { ev.preventDefault(); } });

  // squadre raggruppate per serie
  ["A", "B"].forEach(function (s) {
    var g = document.createElement("optgroup"); g.label = "Serie " + s;
    var seen = {};
    data.players.forEach(function (p) { if (p.s === s) { seen[p.t] = true; } });
    Object.keys(seen).sort(function (x, y) { return x.localeCompare(y); }).forEach(function (t) {
      var o = document.createElement("option"); o.value = t; o.textContent = t; g.appendChild(o);
    });
    if (g.children.length) { el.t.appendChild(g); }
  });

  render();
})();
