/* Логика панели флориста: вход, фильтры, карточки заказов, SSE и звук.
   Список статусов приходит из /api/statuses — в JS он не захардкожен. */

(function () {
  "use strict";

  var MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
  ];

  // ——— Ссылки на элементы разметки ———
  var bootEl = document.getElementById("boot");
  var loginEl = document.getElementById("login");
  var loginForm = document.getElementById("login-form");
  var passwordEl = document.getElementById("password");
  var loginErrorEl = document.getElementById("login-error");
  var appEl = document.getElementById("app");
  var newCountEl = document.getElementById("new-count");
  var newWordEl = document.getElementById("new-word");
  var soundToggle = document.getElementById("sound-toggle");
  var soundLabel = document.getElementById("sound-label");
  var searchEl = document.getElementById("search");
  var filtersEl = document.getElementById("filters");
  var feedEl = document.getElementById("feed");
  var emptyEl = document.getElementById("empty");
  var tabOrders = document.getElementById("tab-orders");
  var tabHandoffs = document.getElementById("tab-handoffs");
  var ordersView = document.getElementById("orders-view");
  var handoffsView = document.getElementById("handoffs-view");
  var handoffsFeedEl = document.getElementById("handoffs-feed");
  var handoffsEmptyEl = document.getElementById("handoffs-empty");
  var handoffCountEl = document.getElementById("handoff-count");
  var toastEl = document.getElementById("toast");
  var toastTextEl = document.getElementById("toast-text");

  // ——— Состояние ———
  var state = {
    statuses: [],
    status: "all",
    q: "",
    tab: "orders",
    sound: true,
    knownIds: null,
    knownHandoffIds: null
  };
  var statusByKey = new Map();
  var cancelledKey = null;
  var initialized = false;
  var eventSource = null;
  var refreshInFlight = false;
  var refreshQueued = false;
  var queuedDetect = false;
  var searchTimer = null;
  var toastTimer = null;
  var audioCtx = null;

  // ——— Вспомогательные функции ———
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function AuthError() {
    this.name = "AuthError";
  }
  AuthError.prototype = Object.create(Error.prototype);

  async function getJSON(url) {
    var res = await fetch(url, { headers: { Accept: "application/json" } });
    if (res.status === 401) throw new AuthError("unauthorized");
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  function parseDate(value) {
    if (!value) return null;
    var d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }

  // Дату доставки разбираем вручную, чтобы не сдвигать день часовым поясом.
  function parseDay(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || "");
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }

  function startOfDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function dayDiff(a, b) {
    return Math.round((startOfDay(a) - startOfDay(b)) / 86400000);
  }

  function timeHHMM(d) {
    var h = String(d.getHours()).padStart(2, "0");
    var m = String(d.getMinutes()).padStart(2, "0");
    return h + ":" + m;
  }

  function formatDelivery(dateValue, timeValue) {
    var day = parseDay(dateValue);
    var label = "";
    if (day) {
      var diff = dayDiff(day, new Date());
      if (diff === 0) label = "сегодня";
      else if (diff === 1) label = "завтра";
      else if (diff === -1) label = "вчера";
      else label = day.getDate() + " " + MONTHS[day.getMonth()];
    }
    var time = timeValue ? String(timeValue).slice(0, 5) : "";
    return [label, time].filter(Boolean).join(" · ") || "—";
  }

  function formatCreated(value) {
    var d = parseDate(value);
    if (!d) return { text: "", fresh: false };
    var now = new Date();
    var diffMs = now.getTime() - d.getTime();
    if (diffMs >= 0 && diffMs < 60000) {
      return { text: "только что", fresh: true };
    }
    var diff = dayDiff(d, now);
    if (diff === 0) return { text: timeHHMM(d), fresh: diffMs < 3600000 };
    if (diff === -1) return { text: "вчера", fresh: false };
    return { text: d.getDate() + " " + MONTHS[d.getMonth()], fresh: false };
  }

  function formatPrice(value) {
    if (value === null || value === undefined || value === "") return "";
    var n = Number(value);
    if (!isFinite(n)) return String(value);
    return n.toLocaleString("ru-RU") + " ₸";
  }

  function formatPhone(phone) {
    var digits = String(phone).replace(/\D/g, "");
    if (digits.length === 11 && digits.charAt(0) === "7") {
      return "+7 " + digits.slice(1, 4) + " " + digits.slice(4, 7) + " " + digits.slice(7);
    }
    return String(phone);
  }

  function telHref(phone) {
    return "tel:" + String(phone).replace(/[^\d+]/g, "");
  }

  function mapsHref(address) {
    return "https://www.google.com/maps/search/?api=1&query=" +
      encodeURIComponent(String(address));
  }

  function countWord(n) {
    return n % 10 === 1 && n % 100 !== 11 ? "новый" : "новых";
  }

  // ——— Звуковые сигналы (только после жеста пользователя) ———
  function audioContext() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  // Общая мелодия из двух sine-тонов с заданной паузой между ними.
  function playToneSequence(freqs, gap) {
    if (!state.sound) return;
    var ctx = audioContext();
    if (!ctx) return;
    var start = ctx.currentTime;
    freqs.forEach(function (freq, i) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      var t = start + i * gap;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.34);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.36);
    });
  }

  // Новый заказ: восходящие тона.
  function playOrderChime() {
    playToneSequence([660, 880], 0.18);
  }

  // Новое обращение: нисходящие тона, чуть длиннее — чтобы отличалось на слух.
  function playHandoffChime() {
    playToneSequence([523, 392], 0.22);
  }

  // Браузеры разрешают звук только после жеста: разблокируем контекст
  // по первому касанию или нажатию клавиши.
  function unlockAudio() {
    audioContext();
    document.removeEventListener("pointerdown", unlockAudio);
    document.removeEventListener("keydown", unlockAudio);
  }
  document.addEventListener("pointerdown", unlockAudio);
  document.addEventListener("keydown", unlockAudio);

  // ——— Тост ———
  function showToast() {
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.hidden = true;
    }, 3400);
  }

  function toastNewOrder(number) {
    toastTextEl.textContent = "";
    var strong = el("strong", null, number);
    toastTextEl.appendChild(strong);
    toastTextEl.appendChild(document.createTextNode(" — новый заказ"));
    showToast();
  }

  function toastNewHandoff(handoff) {
    toastTextEl.textContent = "";
    var strong = el("strong", null, handoff.client_name || "Клиент");
    toastTextEl.appendChild(strong);
    toastTextEl.appendChild(document.createTextNode(" — новое обращение"));
    showToast();
  }

  function toastMessage(text) {
    toastTextEl.textContent = text;
    showToast();
  }

  // ——— Карточка заказа ———
  function statusPill(key) {
    var info = statusByKey.get(key);
    var pill = el("span", "status");
    pill.style.setProperty("--st-color", "var(--st-" + key + ", var(--muted))");
    pill.style.setProperty("--st-bg", "var(--st-" + key + "-bg, var(--surface-2))");
    pill.appendChild(el("span", "dot"));
    pill.appendChild(document.createTextNode(info ? info.label : (key || "—")));
    return pill;
  }

  // Стебель строится из статусов API: путь без «cancelled», последний — цветок.
  function growth(statusKey) {
    var path = state.statuses.filter(function (s) { return s.key !== "cancelled"; });
    var isCancelled = statusKey === "cancelled";
    var index = -1;
    path.forEach(function (s, i) { if (s.key === statusKey) index = i; });

    var node = el("div", "growth" + (isCancelled ? " growth--cancelled" : ""));
    path.forEach(function (s, i) {
      var isBloom = i === path.length - 1;
      var mark = el("span", isBloom ? "g-bloom" : "g-node");
      if (!isCancelled && index >= 0) {
        var on = isBloom ? i === index : i <= index;
        if (on) mark.classList.add("on");
      }
      node.appendChild(mark);
    });
    return node;
  }

  function buildRow(label, value) {
    var row = el("div", "row");
    row.appendChild(el("span", "k", label));
    var v = el("span", "v");
    if (value instanceof Node) v.appendChild(value);
    else v.textContent = value === null || value === undefined ? "" : String(value);
    row.appendChild(v);
    return row;
  }

  function phoneLink(phone) {
    var link = el("a", null, formatPhone(phone));
    link.href = telHref(phone);
    return link;
  }

  function bouquetValue(order) {
    var v = el("span", "v bouquet");
    var name = order.bouquet_name ? "«" + order.bouquet_name + "»" : "—";
    v.appendChild(document.createTextNode(name));
    var price = formatPrice(order.price_kzt);
    if (price) {
      v.appendChild(document.createTextNode(" · "));
      v.appendChild(el("span", "price", price));
    }
    return v;
  }

  function noteBox(label, text) {
    var note = el("div", "cardnote");
    note.appendChild(el("b", null, label + ":"));
    note.appendChild(document.createTextNode(" " + text));
    return note;
  }

  function linkButton(text, href) {
    var link = el("a", "btn--link", text);
    link.href = href;
    return link;
  }

  function buildCard(order, isNew) {
    var card = el("article", "card" + (isNew ? " card--new" : ""));
    card.dataset.id = String(order.id);
    card.dataset.status = order.status || "";

    var top = el("div", "card__top");
    top.appendChild(el("span", "card__num", order.public_number || "—"));
    top.appendChild(statusPill(order.status));
    var created = formatCreated(order.created_at);
    top.appendChild(el(
      "span",
      "card__time" + (created.fresh ? " card__time--fresh" : ""),
      created.text
    ));
    card.appendChild(top);

    card.appendChild(growth(order.status));

    var meta = el("div", "meta");
    meta.appendChild(el("div", "client", order.client_name || "Клиент"));
    if (order.phone) meta.appendChild(buildRow("Телефон", phoneLink(order.phone)));
    meta.appendChild(buildRow("Букет", bouquetValue(order)));
    if (order.delivery_date || order.delivery_time) {
      meta.appendChild(buildRow(
        "Доставка",
        formatDelivery(order.delivery_date, order.delivery_time)
      ));
    }
    if (order.address) meta.appendChild(buildRow("Адрес", order.address));
    if (order.is_gift) {
      var recipient = [order.recipient_name, order.recipient_phone]
        .filter(Boolean)
        .join(" · ");
      if (recipient) meta.appendChild(buildRow("Получатель", recipient));
    }
    card.appendChild(meta);

    if (order.card_text) {
      card.appendChild(noteBox("Открытка", order.card_text));
    }
    if (order.comment) {
      card.appendChild(noteBox("Комментарий", order.comment));
    }

    var links = el("div", "actions");
    if (order.phone) links.appendChild(linkButton("Позвонить", telHref(order.phone)));
    if (order.address) links.appendChild(linkButton("Карты", mapsHref(order.address)));
    if (links.childElementCount) card.appendChild(links);

    // Кнопки статуса строятся из /api/statuses.
    var current = statusByKey.get(order.status);
    var index = -1;
    state.statuses.forEach(function (s, i) {
      if (s.key === order.status) index = i;
    });
    var next = index >= 0 ? state.statuses[index + 1] : null;

    if (current && current.next_action && next) {
      var actions = el("div", "actions");
      var primary = el("button", "btn btn--primary", current.next_action);
      primary.type = "button";
      primary.addEventListener("click", function () {
        setStatus(order, next.key, primary);
      });
      actions.appendChild(primary);

      if (cancelledKey) {
        var cancel = el("button", "btn btn--ghost", "Отменить");
        cancel.type = "button";
        cancel.addEventListener("click", function () {
          setStatus(order, cancelledKey, cancel);
        });
        actions.appendChild(cancel);
      }
      card.appendChild(actions);
    } else {
      var isCancelled = order.status === cancelledKey;
      card.appendChild(el(
        "div",
        "done-note" + (isCancelled ? " done-note--cancelled" : ""),
        current ? current.label : (order.status || "Завершён")
      ));
    }

    return card;
  }

  // ——— Карточка обращения ———
  function handoffStatusPill(status) {
    var resolved = status === "resolved";
    var pill = el("span", "status");
    // Используем тот же механизм, что у заказов: цвет берётся из --st-color.
    pill.style.setProperty("--st-color", resolved ? "var(--st-cancelled)" : "var(--st-new)");
    pill.style.setProperty("--st-bg", resolved ? "var(--st-cancelled-bg)" : "var(--st-new-bg)");
    pill.appendChild(el("span", "dot"));
    pill.appendChild(document.createTextNode(resolved ? "Решено" : "Новое"));
    return pill;
  }

  function buildHandoffCard(handoff, isNew) {
    var card = el("article", "card" + (isNew ? " card--new" : ""));
    card.dataset.id = String(handoff.id);
    card.dataset.status = handoff.status || "";

    var top = el("div", "card__top");
    top.appendChild(el("span", "card__num", "Обращение"));
    top.appendChild(handoffStatusPill(handoff.status));
    var created = formatCreated(handoff.created_at);
    top.appendChild(el(
      "span",
      "card__time" + (created.fresh ? " card__time--fresh" : ""),
      created.text
    ));
    card.appendChild(top);

    if (handoff.reason) {
      card.appendChild(el("div", "handoff__reason", handoff.reason));
    }

    var meta = el("div", "meta");
    meta.appendChild(el("div", "client", handoff.client_name || "Клиент"));
    card.appendChild(meta);

    if (handoff.last_message) {
      card.appendChild(el("div", "handoff__msg", handoff.last_message));
    }

    if (handoff.status === "new") {
      var actions = el("div", "actions");
      var resolve = el("button", "btn btn--primary", "Решено");
      resolve.type = "button";
      resolve.addEventListener("click", function () {
        resolveHandoff(handoff, resolve);
      });
      actions.appendChild(resolve);
      card.appendChild(actions);
    } else {
      card.appendChild(el("div", "done-note", "Решено"));
    }

    return card;
  }

  // ——— Отрисовка ———
  function renderFeed(orders, newIds) {
    feedEl.textContent = "";
    var fragment = document.createDocumentFragment();
    orders.forEach(function (order) {
      fragment.appendChild(buildCard(order, newIds.has(order.id)));
    });
    feedEl.appendChild(fragment);

    emptyEl.hidden = orders.length !== 0;
    if (!orders.length) {
      emptyEl.textContent = state.q ? "Ничего не найдено" : "Заказов пока нет";
    }

    if (newIds.size) {
      setTimeout(function () {
        Array.prototype.forEach.call(
          feedEl.querySelectorAll(".card--new"),
          function (card) { card.classList.remove("card--new"); }
        );
      }, 2600);
    }
  }

  function renderHandoffs(handoffs, newIds) {
    handoffsFeedEl.textContent = "";
    var fragment = document.createDocumentFragment();
    handoffs.forEach(function (handoff) {
      fragment.appendChild(buildHandoffCard(handoff, newIds.has(handoff.id)));
    });
    handoffsFeedEl.appendChild(fragment);

    handoffsEmptyEl.hidden = handoffs.length !== 0;
    handoffsEmptyEl.textContent = "Обращений пока нет";

    if (newIds.size) {
      setTimeout(function () {
        Array.prototype.forEach.call(
          handoffsFeedEl.querySelectorAll(".card--new"),
          function (card) { card.classList.remove("card--new"); }
        );
      }, 2600);
    }
  }

  function updateHandoffCount(handoffs) {
    var count = 0;
    handoffs.forEach(function (handoff) {
      if (handoff.status === "new") count += 1;
    });
    handoffCountEl.textContent = String(count);
    handoffCountEl.hidden = count === 0;
  }

  function updateBadge(allOrders) {
    var count = 0;
    allOrders.forEach(function (order) {
      if (order.status === "new") count += 1;
    });
    newCountEl.textContent = String(count);
    newWordEl.textContent = countWord(count);
  }

  function updateChipCounts(allOrders) {
    var counts = new Map();
    allOrders.forEach(function (order) {
      counts.set(order.status, (counts.get(order.status) || 0) + 1);
    });
    Array.prototype.forEach.call(filtersEl.querySelectorAll(".chip"), function (chip) {
      var key = chip.dataset.filter;
      var n = chip.querySelector(".n");
      if (!n) return;
      n.textContent = String(key === "all" ? allOrders.length : (counts.get(key) || 0));
    });
  }

  // ——— Загрузка заказов ———
  function ordersUrl(status, q) {
    var params = new URLSearchParams();
    params.set("status", status || "all");
    if (q) params.set("q", q);
    return "/api/orders?" + params.toString();
  }

  async function refresh(detectNew) {
    if (refreshInFlight) {
      refreshQueued = true;
      queuedDetect = queuedDetect || detectNew;
      return;
    }
    refreshInFlight = true;
    try {
      var needAll = !(state.status === "all" && !state.q);
      var url = ordersUrl(state.status, state.q);
      var results = await Promise.all([
        getJSON(url),
        needAll ? getJSON("/api/orders?status=all") : Promise.resolve(null)
      ]);
      var display = results[0];
      var all = results[1];
      var allOrders = all ? all.orders : display.orders;

      var newOnes = [];
      if (state.knownIds === null) {
        // Первая загрузка задаёт базовый список — без тоста.
        state.knownIds = new Set(allOrders.map(function (o) { return o.id; }));
      } else if (detectNew) {
        newOnes = allOrders.filter(function (o) {
          return !state.knownIds.has(o.id);
        });
        state.knownIds = new Set(allOrders.map(function (o) { return o.id; }));
      }

      renderFeed(display.orders, new Set(newOnes.map(function (o) { return o.id; })));
      updateBadge(allOrders);
      updateChipCounts(allOrders);

      if (newOnes.length) {
        toastNewOrder(newOnes[0].public_number || "Заказ");
        playOrderChime();
      }
      return true;
    } catch (err) {
      if (err instanceof AuthError) {
        showLogin();
      } else {
        toastMessage("Не удалось загрузить заказы");
      }
      return false;
    } finally {
      refreshInFlight = false;
      if (refreshQueued) {
        var detect = queuedDetect;
        refreshQueued = false;
        queuedDetect = false;
        refresh(detect);
      }
    }
  }

  // ——— Загрузка обращений ———
  async function loadHandoffs(detectNew) {
    try {
      var data = await getJSON("/api/handoffs?status=all");
      var handoffs = Array.isArray(data.handoffs) ? data.handoffs : [];

      var newOnes = [];
      if (state.knownHandoffIds === null) {
        // Первая загрузка задаёт базовый список — без тоста.
        state.knownHandoffIds = new Set(handoffs.map(function (h) { return h.id; }));
      } else if (detectNew) {
        newOnes = handoffs.filter(function (h) {
          return !state.knownHandoffIds.has(h.id);
        });
        state.knownHandoffIds = new Set(handoffs.map(function (h) { return h.id; }));
      }

      renderHandoffs(handoffs, new Set(newOnes.map(function (h) { return h.id; })));
      updateHandoffCount(handoffs);

      if (newOnes.length) {
        toastNewHandoff(newOnes[0]);
        playHandoffChime();
      }
      return true;
    } catch (err) {
      if (err instanceof AuthError) {
        showLogin();
      } else {
        toastMessage("Не удалось загрузить обращения");
      }
      return false;
    }
  }

  // ——— Закрытие обращения ———
  async function resolveHandoff(handoff, button) {
    button.disabled = true;
    try {
      var res = await fetch("/api/handoffs/" + handoff.id + "/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" }
      });
      if (res.status === 401) {
        showLogin();
        return;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      await loadHandoffs(false);
    } catch (err) {
      button.disabled = false;
      toastMessage("Не удалось закрыть обращение");
    }
  }

  // ——— Смена статуса ———
  async function setStatus(order, newStatus, button) {
    button.disabled = true;
    try {
      var res = await fetch("/api/orders/" + order.id + "/status", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ status: newStatus })
      });
      if (res.status === 401) {
        showLogin();
        return;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      await refresh(false);
    } catch (err) {
      button.disabled = false;
      toastMessage("Не удалось изменить статус");
    }
  }

  // ——— Чипы фильтра ———
  function buildChip(key, label, isAll) {
    var chip = el("button", "chip");
    chip.type = "button";
    chip.dataset.filter = key;
    var active = state.status === key;
    if (active) chip.classList.add("active");
    chip.setAttribute("aria-pressed", String(active));

    if (!isAll) {
      var dot = el("span", "dot");
      dot.style.setProperty("--dot", "var(--st-" + key + ", var(--faint))");
      chip.appendChild(dot);
    }
    chip.appendChild(document.createTextNode(label));
    chip.appendChild(document.createTextNode(" "));
    chip.appendChild(el("span", "n", "0"));

    chip.addEventListener("click", function () {
      selectChip(key);
    });
    return chip;
  }

  function buildChips() {
    filtersEl.textContent = "";
    filtersEl.appendChild(buildChip("all", "Все", true));
    state.statuses.forEach(function (s) {
      filtersEl.appendChild(buildChip(s.key, s.label, false));
    });
  }

  function selectChip(key) {
    state.status = key;
    Array.prototype.forEach.call(filtersEl.querySelectorAll(".chip"), function (chip) {
      var on = chip.dataset.filter === key;
      chip.classList.toggle("active", on);
      chip.setAttribute("aria-pressed", String(on));
    });
    refresh(false);
  }

  // ——— SSE ———
  function connectEvents() {
    if (eventSource || !window.EventSource) return;
    eventSource = new EventSource("/api/events");
    eventSource.onmessage = function (event) {
      var data = (event.data || "").trim();
      if (data === "refresh") refresh(true);
      else if (data === "refresh_handoffs") loadHandoffs(true);
    };
    // При обрыве EventSource переподключается сам.
    eventSource.onerror = function () {};
  }

  // ——— Экраны ———
  function showLogin() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    initialized = false;
    state.knownIds = null;
    state.knownHandoffIds = null;
    appEl.hidden = true;
    bootEl.hidden = true;
    loginEl.hidden = false;
    loginErrorEl.hidden = true;
    if (passwordEl) passwordEl.value = "";
  }

  function showPanel() {
    bootEl.hidden = true;
    loginEl.hidden = true;
    appEl.hidden = false;
  }

  async function init() {
    if (initialized) return;
    initialized = true;
    state.status = "all";
    state.q = "";
    state.sound = true;
    state.knownIds = null;
    state.knownHandoffIds = null;
    if (searchEl) searchEl.value = "";
    feedEl.textContent = "";
    emptyEl.hidden = false;
    emptyEl.textContent = "Загрузка…";
    handoffsFeedEl.textContent = "";
    handoffsEmptyEl.hidden = true;
    handoffCountEl.textContent = "0";
    handoffCountEl.hidden = true;
    selectTab("orders");

    try {
      var data = await getJSON("/api/statuses");
      state.statuses = Array.isArray(data.statuses) ? data.statuses : [];
      statusByKey = new Map();
      state.statuses.forEach(function (s) {
        statusByKey.set(s.key, s);
      });
      cancelledKey = statusByKey.has("cancelled") ? "cancelled" : null;

      buildChips();
      var ok = await refresh(false);
      if (appEl.hidden) return; // сессия истекла — уже показан вход
      if (!ok) {
        feedEl.textContent = "";
        emptyEl.hidden = false;
        emptyEl.textContent = "Не удалось загрузить заказы. Обновите страницу.";
        return;
      }
      connectEvents();
    } catch (err) {
      if (err instanceof AuthError) {
        showLogin();
        return;
      }
      feedEl.textContent = "";
      emptyEl.hidden = false;
      emptyEl.textContent = "Не удалось загрузить заказы. Обновите страницу.";
    }
  }

  // ——— Вход ———
  async function checkAuth() {
    try {
      var res = await fetch("/api/me", { headers: { Accept: "application/json" } });
      if (res.ok) {
        showPanel();
        await init();
      } else {
        showLogin();
      }
    } catch (err) {
      showLogin();
    }
  }

  loginForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var button = loginForm.querySelector("button[type='submit']");
    button.disabled = true;
    loginErrorEl.hidden = true;
    try {
      var res = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ password: passwordEl.value })
      });
      if (res.ok) {
        showPanel();
        await init();
      } else if (res.status === 401) {
        loginErrorEl.textContent = "Неверный пароль";
        loginErrorEl.hidden = false;
      } else {
        loginErrorEl.textContent = "Не удалось войти. Попробуйте ещё раз.";
        loginErrorEl.hidden = false;
      }
    } catch (err) {
      loginErrorEl.textContent = "Не удалось войти. Проверьте соединение.";
      loginErrorEl.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  // ——— Поиск с задержкой ———
  searchEl.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.q = searchEl.value.trim();
      refresh(false);
    }, 300);
  });

  // ——— Вкладки ———
  function selectTab(tab) {
    state.tab = tab;
    var ordersOn = tab === "orders";
    tabOrders.classList.toggle("active", ordersOn);
    tabHandoffs.classList.toggle("active", !ordersOn);
    tabOrders.setAttribute("aria-selected", String(ordersOn));
    tabHandoffs.setAttribute("aria-selected", String(!ordersOn));
    ordersView.hidden = !ordersOn;
    handoffsView.hidden = ordersOn;
    // При первом открытии вкладки «Обращения» подгружаем список.
    if (!ordersOn && state.knownHandoffIds === null) loadHandoffs(false);
  }

  tabOrders.addEventListener("click", function () {
    selectTab("orders");
  });

  tabHandoffs.addEventListener("click", function () {
    selectTab("handoffs");
  });

  // ——— Переключатель звука ———
  function updateSoundToggle() {
    soundToggle.setAttribute("aria-pressed", String(state.sound));
    soundToggle.setAttribute(
      "aria-label",
      state.sound ? "Звук включён" : "Звук выключен"
    );
    if (soundLabel) soundLabel.textContent = state.sound ? "Звук вкл" : "Звук выкл";
  }

  soundToggle.addEventListener("click", function () {
    var wasOff = !state.sound;
    state.sound = !state.sound;
    updateSoundToggle();
    // Включаем вручную — даём пробный сигнал нового заказа.
    if (wasOff) playOrderChime();
  });

  // Звук включён по умолчанию — синхронизируем разметку и состояние.
  state.sound = true;
  updateSoundToggle();

  // ——— Старт ———
  checkAuth();
})();
