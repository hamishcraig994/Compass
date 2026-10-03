/* What's Next - progressive enhancement. Plain JS, no libraries.
 *
 * Every form and link on the page works without this file (POST + 303 redirect). This only
 * intercepts them to work in place: forms marked data-enhance="dismiss|undismiss|add|refresh|generate|rate|theme",
 * "Add to library" links (data-add-dialog), card details (a modal instead of the inline <details>),
 * and the "Finding your recommendations..." / "Updating..." states (data-poll), which poll /api/status.
 *
 * DOM is built with createElement/textContent. The one exception is the add dialog fragment from
 * our own server (/add-dialog?...&partial=1), which is rendered and escaped server-side.
 */
(function () {
  "use strict";

  var doc = document;
  doc.documentElement.classList.add("js");
  if (!window.fetch || !window.URLSearchParams || !window.FormData || !window.Promise) return;  // plain forms still work

  var POLL_MS = 3000;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var hasDialog = typeof window.HTMLDialogElement === "function";
  if (hasDialog) doc.documentElement.classList.add("has-dialog");  // CSS hides the row cards' <details> then
  var heroApi = null;  // set by the hero controller below; syncHero() reaches it after a card is removed

  /* ---------------- helpers ---------------- */

  function el(tag, props, children) {
    var node = doc.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var value = props[key];
        if (key === "text") node.textContent = value;
        else if (key === "className") node.className = value;
        else node.setAttribute(key, value);
      });
    }
    (children || []).forEach(function (child) { if (child) node.appendChild(child); });
    return node;
  }

  function visible(node) {
    return !!(node && node.getClientRects().length);
  }

  function field(form, name) {
    var input = form.elements[name];
    return input ? input.value : "";
  }

  function post(url, body, keepalive) {
    return fetch(url, {
      keepalive: !!keepalive,
      method: "POST",
      credentials: "same-origin",
      headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString()
    }).then(function (response) {
      var type = response.headers.get("Content-Type") || "";
      if (type.indexOf("application/json") === -1) {
        var err = new Error("Not a JSON response");
        err.notJson = true;
        throw err;
      }
      return response.json();  // 400s carry {"ok": false, "message": ...} too
    });
  }

  function postForm(form) {
    return post(form.getAttribute("action"), new URLSearchParams(new FormData(form)));
  }

  /* If the server didn't answer with JSON, fall back to the ordinary form submission; on a
   * network error just say so and leave the page as it is. */
  function failed(form, err) {
    if (err && err.notJson && form) {
      nativeSubmit(form);
      return;
    }
    toast("Couldn't reach the server - try again.", { error: true });
  }

  /* Native submit - doesn't re-trigger our listener. A form cloned into the modal is detached
   * once the modal closes (closing empties its body), and browsers silently drop submits of
   * detached forms, so in that case rebuild it as a hidden form attached to the page. */
  function nativeSubmit(form) {
    if (doc.body.contains(form)) { form.submit(); return; }
    var copy = el("form", { method: "post", action: form.getAttribute("action") || "", hidden: "" });
    new FormData(form).forEach(function (value, name) {
      copy.appendChild(el("input", { type: "hidden", name: name, value: String(value) }));
    });
    doc.body.appendChild(copy);
    copy.submit();
  }

  function setBusy(form, busy) {
    var button = form.querySelector("button[type=submit], button:not([type])");
    if (busy) {
      form.setAttribute("data-busy", "1");
      if (button) button.setAttribute("aria-busy", "true");
    } else {
      form.removeAttribute("data-busy");
      if (button) button.removeAttribute("aria-busy");
    }
  }

  /* Every on-page card for one title: its hero slide plus each row it appears in (never copies in the modal/preview). */
  function findCards(type, id) {
    var key = type + "-" + id;
    return Array.prototype.filter.call(doc.querySelectorAll("[data-card]"), function (card) {
      return card.getAttribute("data-card") === key && !card.closest("dialog, .preview");
    });
  }

  function finePointer() {
    return !!(window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches);
  }

  function animate(node, className) {
    return new Promise(function (resolve) {
      if (reduceMotion) { resolve(); return; }
      var done = false;
      function finish() {
        if (done) return;
        done = true;
        node.removeEventListener("animationend", finish);
        resolve();
      }
      node.addEventListener("animationend", finish);
      node.classList.add(className);
      setTimeout(finish, 450);
    });
  }

  /* ---------------- toasts (aria-live region rendered by _shell) ---------------- */

  var region = doc.getElementById("toasts");
  if (!region) {
    region = el("div", { className: "toasts", id: "toasts", role: "status", "aria-live": "polite" });
    doc.body.appendChild(region);
  }

  function hideToast(node) {
    if (!node.parentNode) return;
    clearTimeout(node._timer);
    node.classList.add("is-hiding");
    setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, reduceMotion ? 0 : 220);
  }

  /* opts: {error, action: {label, run}, duration (ms; 0 = stays until closed)} */
  function toast(message, opts) {
    opts = opts || {};
    var node = el("div", { className: "toast" + (opts.error ? " error" : "") }, [
      el("span", { className: "toast-msg", text: message || (opts.error ? "Something went wrong." : "Done.") })
    ]);
    if (opts.action) {
      var action = el("button", { type: "button", className: "toast-action", text: opts.action.label });
      action.addEventListener("click", function () { hideToast(node); opts.action.run(); });
      node.appendChild(action);
    }
    var close = el("button", { type: "button", "aria-label": "Close notification", text: "×" });
    close.addEventListener("click", function () { hideToast(node); });
    node.appendChild(close);

    var duration = opts.duration === undefined ? (opts.action ? 8000 : 4500) : opts.duration;
    function arm(ms) {
      if (!duration) return;
      clearTimeout(node._timer);
      node._timer = setTimeout(function () { hideToast(node); }, ms);
    }
    // Don't vanish from under someone reaching for Undo with the mouse or keyboard.
    node.addEventListener("mouseenter", function () { clearTimeout(node._timer); });
    node.addEventListener("focusin", function () { clearTimeout(node._timer); });
    node.addEventListener("mouseleave", function () { if (!node.contains(doc.activeElement)) arm(3000); });
    node.addEventListener("focusout", function (e) { if (!node.contains(e.relatedTarget)) arm(3000); });

    while (region.children.length >= 3) region.removeChild(region.firstChild);
    region.appendChild(node);
    arm(duration);
    return node;
  }

  /* ---------------- modal ---------------- */

  var modal = null;
  var modalBody = null;
  var returnFocus = null;

  function focusables(root) {
    return Array.prototype.filter.call(
      root.querySelectorAll("a[href], button:not([disabled]), input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex='-1'])"),
      visible);
  }

  function trapFocus(e) {
    var items = focusables(modal);
    if (!items.length) return;
    var first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (doc.activeElement === first || !modal.contains(doc.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (doc.activeElement === last || !modal.contains(doc.activeElement))) { e.preventDefault(); first.focus(); }
  }

  function ensureModal() {
    if (modal) return;
    modal = el("dialog", { className: "modal", "aria-modal": "true" });
    var inner = el("div", { className: "modal-inner" });
    var close = el("button", { type: "button", className: "modal-close", "aria-label": "Close", text: "×" });
    close.addEventListener("click", closeModal);
    modalBody = el("div", { className: "modal-body" });
    inner.appendChild(close);
    inner.appendChild(modalBody);
    modal.appendChild(inner);
    // A click on the backdrop lands on the <dialog> itself (outside .modal-inner): close.
    modal.addEventListener("click", function (e) { if (e.target === modal) closeModal(); });
    modal.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); closeModal(); }
      else if (e.key === "Tab") trapFocus(e);
    });
    modal.addEventListener("cancel", function (e) { e.preventDefault(); closeModal(); });
    modal.addEventListener("close", function () {
      modalBody.textContent = "";
      var target = returnFocus;
      returnFocus = null;
      if (target && doc.contains(target) && visible(target)) target.focus();
      else { var main = doc.getElementById("main"); if (main) main.focus(); }
    });
    doc.body.appendChild(modal);
  }

  /* fill(body) puts content into the modal; opts.focus picks what gets focus first. */
  function openModal(fill, opts) {
    opts = opts || {};
    closePreview();
    ensureModal();
    if (!modal.open) returnFocus = opts.returnFocus || doc.activeElement;
    modalBody.textContent = "";
    modal.className = "modal" + (opts.narrow ? " narrow" : "");
    fill(modalBody);
    var label = modalBody.querySelector("h3[id]");
    if (label) modal.setAttribute("aria-labelledby", label.id); else modal.removeAttribute("aria-labelledby");
    if (!modal.open) modal.showModal();
    var first = (opts.focus && modalBody.querySelector(opts.focus)) ||
                (opts.closeFallback ? modal.querySelector(".modal-close") : focusables(modalBody)[0]) ||
                modal.querySelector(".modal-close");
    if (first) first.focus();
  }

  function closeModal() {
    closePreview();
    if (modal && modal.open) modal.close();
  }

  /* ---------------- card details ---------------- */

  function openDetail(card, trigger) {
    openModal(function (body) {
      var poster = card.querySelector(".card-poster");
      var posterCopy = poster ? poster.cloneNode(true) : null;
      if (posterCopy) {
        posterCopy.removeAttribute("data-open-detail");
        posterCopy.removeAttribute("hidden");  // the hero's copy is hidden on the page, shown in the modal
        posterCopy.removeAttribute("role");
        posterCopy.removeAttribute("tabindex");
        posterCopy.removeAttribute("aria-label");
        var rank = posterCopy.querySelector(".rank");
        if (rank) rank.parentNode.removeChild(rank);
      }
      var title = card.querySelector(".title");
      var main = el("div", { className: "detail-main" }, [
        el("h3", { id: "detail-title", text: title ? title.textContent : "" })
      ]);
      var detail = card.querySelector(".detail-body");
      if (detail) main.appendChild(detail.cloneNode(true));
      var actions = card.querySelector(".card-actions");
      if (actions) main.appendChild(actions.cloneNode(true));  // delegated handlers find the card by type/id
      body.appendChild(el("div", { className: "detail-layout" }, [posterCopy, main]));
    }, { returnFocus: trigger, closeFallback: true,
       focus: ".detail-main .btn-add, .detail-main form[data-enhance=dismiss] button[type=submit]" });
  }

  /* ---------------- add-to-library dialog ---------------- */

  function openAddDialog(link) {
    var href = link.getAttribute("href");
    // A link cloned into the (hidden) hover preview can't take focus back: use its card's poster button.
    var fromPreview = link.closest(".preview") && lastPreviewCard;
    var returnTarget = (fromPreview && fromPreview.querySelector("[data-open-detail]")) || link;
    openModal(function (body) {
      body.appendChild(el("div", { className: "modal-loading", role: "status" }, [
        el("div", { className: "spinner", "aria-hidden": "true" }),
        el("span", { text: "Loading options..." })
      ]));
    }, { narrow: true, returnFocus: returnTarget, focus: ".modal-close" });

    fetch(href + (href.indexOf("?") === -1 ? "?" : "&") + "partial=1", {
      credentials: "same-origin", headers: { "Accept": "text/html" }
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.text();
    }).then(function (html) {
      if (!modal || !modal.open) return;  // closed while loading
      if (/<html[\s>]/i.test(html)) { window.location.href = href; return; }  // no partial support: use the page
      openModal(function (body) {
        // Trusted: our own server's fragment, escaped server-side (see render_add_dialog).
        body.innerHTML = html;
      }, { narrow: true, focus: "select, input:not([type=hidden]), .btn-add" });
    }).catch(function () {
      window.location.href = href;  // the plain page version
    });
  }

  doc.addEventListener("click", function (e) {
    var target = e.target;
    if (!target || !target.closest) return;
    var plainClick = e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey;

    var link = target.closest("a[data-add-dialog]");
    if (link && hasDialog && plainClick) {
      e.preventDefault();
      openAddDialog(link);
      return;
    }
    var closer = target.closest("dialog [data-close]");
    if (closer && plainClick) { e.preventDefault(); closeModal(); return; }

    if (!hasDialog || target.closest("dialog")) return;
    var summary = target.closest(".card-details > summary");
    var poster = target.closest("[data-open-detail]");
    var card = (summary || poster) && target.closest("[data-card]");
    if (!card) return;
    e.preventDefault();
    var opener = card.querySelector("[data-open-detail]");
    openDetail(card, summary && visible(summary) ? summary : (opener || summary));
  });

  /* With a dialog, a row card's poster is its one tab stop: a button that opens the detail modal. */
  function titleOf(card) {
    var title = card.querySelector(".title");
    return title ? title.textContent : "";
  }

  if (hasDialog) {
    Array.prototype.forEach.call(doc.querySelectorAll(".row-card[data-card] [data-open-detail]"), function (poster) {
      poster.setAttribute("tabindex", "0");
      poster.setAttribute("role", "button");
      poster.setAttribute("aria-label", "More info: " + titleOf(poster.closest("[data-card]")));
    });
    doc.addEventListener("keydown", function (e) {
      if ((e.key !== "Enter" && e.key !== " ") || !e.target || !e.target.matches ||
          !e.target.matches("[data-open-detail][role=button]")) return;
      var card = e.target.closest("[data-card]");
      if (!card) return;
      e.preventDefault();
      openDetail(card, e.target);
    });
  }

  /* ---------------- removing / restoring cards ---------------- */

  function syncEmpty(grid) {
    if (!grid || !grid.parentNode) return;
    if (grid.hasAttribute("data-hero-slides")) { syncHero(); syncBrowseEmpty(); return; }
    if (grid.hasAttribute("data-track")) {
      var row = grid.closest(".row");
      if (row) row.hidden = !grid.querySelector(".row-card");
      if (grid.classList.contains("track-numbered")) {
        Array.prototype.forEach.call(grid.querySelectorAll(".rank"), function (rank, i) { rank.textContent = String(i + 1); });
      }
      updateArrows(grid);
      syncBrowseEmpty();
      return;
    }
    var existing = grid.parentNode.querySelector("[data-js-empty]");
    var hasCards = !!grid.querySelector("[data-card]");
    if (hasCards && existing) existing.parentNode.removeChild(existing);
    if (!hasCards && !existing) {
      grid.parentNode.insertBefore(el("div", { className: "empty", "data-js-empty": "" }, [
        el("h3", { text: "That's everything here" }),
        el("p", { text: "Refresh to look for more, or try another tab." })
      ]), grid.nextSibling);
    }
  }

  /* Browse pages: once the last card is gone, say so (the hero and every row are hidden by then). */
  function syncBrowseEmpty() {
    var top = doc.querySelector("body.cinematic .browse-top");
    if (!top) return;
    var existing = doc.querySelector("[data-js-empty]");
    var hasCards = !!doc.querySelector("main .row:not([hidden]), main [data-hero]:not([hidden])");
    if (hasCards && existing) existing.parentNode.removeChild(existing);
    if (!hasCards && !existing) {
      top.parentNode.insertBefore(el("div", { className: "empty", "data-js-empty": "" }, [
        el("h3", { text: "That's everything here" }),
        el("p", { text: "Refresh to look for more." })
      ]), top);
    }
  }

  function syncHero() {
    if (heroApi) heroApi.sync();
  }

  function focusNeighbour(card) {
    var next = card.nextElementSibling || card.previousElementSibling;
    var target = next && (next.querySelector("[role=button]") || next.querySelector("summary") || next.querySelector("a, button"));
    if (target) target.focus();
    if (!target || card.contains(doc.activeElement)) { var main = doc.getElementById("main"); if (main) main.focus(); }
  }

  function removeCard(card) {
    var place = { parent: card.parentNode, next: card.nextSibling };
    if (previewCard === card) closePreview();
    return animate(card, "is-leaving").then(function () {
      if (card.contains(doc.activeElement)) focusNeighbour(card);
      if (card.parentNode) card.parentNode.removeChild(card);
      card.classList.remove("is-leaving");
      syncEmpty(place.parent);
      return place;
    });
  }

  function removeCards(cards) {
    return Promise.all(cards.map(removeCard));
  }

  function reinsertCards(cards, places) {
    var stale = places.some(function (place) { return !place || !place.parent || !doc.contains(place.parent); });
    if (!cards.length || stale) { window.location.reload(); return; }
    // Later siblings first, so each card finds its own "next" still in place.
    for (var i = cards.length - 1; i >= 0; i--) {
      var card = cards[i], place = places[i];
      var next = place.next && place.next.parentNode === place.parent ? place.next : null;
      place.parent.insertBefore(card, next);
      syncEmpty(place.parent);
      animate(card, "is-entering").then(function (c) { return function () { c.classList.remove("is-entering"); }; }(card));
    }
    // Prefer a visible row card's poster button (the hero slide may be inert or off-screen).
    var focusTarget = null;
    for (var j = 0; j < cards.length && !focusTarget; j++) {
      var candidate = cards[j].querySelector("[role=button]");
      if (candidate && visible(candidate)) focusTarget = candidate;
    }
    for (var k = 0; k < cards.length && !focusTarget; k++) {
      var fallback = cards[k].querySelector("summary");
      if (fallback && visible(fallback)) focusTarget = fallback;
    }
    if (focusTarget) focusTarget.focus();
    else { var main = doc.getElementById("main"); if (main) main.focus(); }
  }

  function undismiss(undo, returnTo, cards, places) {
    var body = new URLSearchParams();
    body.set("type", undo.type);
    body.set("id", String(undo.id));
    body.set("return_to", returnTo || window.location.pathname + window.location.search);
    post("/undismiss", body).then(function (data) {
      if (!data.ok) { toast(data.message || "Couldn't undo that.", { error: true }); return; }
      if (cards && cards.length && places) reinsertCards(cards, places); else window.location.reload();
      toast(data.message || "Restored");
    }).catch(function () { toast("Couldn't reach the server - try again.", { error: true }); });
  }

  /* ---------------- enhanced forms ---------------- */

  /* Redraw one rate form from the server's answer: mine = personal stars or null, plex = Plex's 1-5 or null. */
  function applyRating(form, mine, plex) {
    var effective = mine || plex || 0;
    var fromPlex = !mine && !!plex;
    Array.prototype.forEach.call(form.querySelectorAll(".star"), function (star) {
      var n = parseInt(star.value, 10);
      star.classList.toggle("on", n <= effective);
      star.classList.toggle("from-plex", n <= effective && fromPlex);
      star.setAttribute("aria-pressed", n === mine ? "true" : "false");
    });
    var text = form.querySelector(".rating-text");
    if (text) text.textContent = mine ? "Your rating: " + mine + "/5" : (plex ? "From Plex: " + plex + "/5" : "Not rated");
    var clear = form.querySelector(".star-clear");
    if (mine && !clear) {
      var clearButton = el("button", { type: "submit", name: "stars", value: "0", className: "link-btn star-clear", text: "Clear rating" });
      if (form.closest(".preview")) clearButton.setAttribute("tabindex", "-1");  // keyboard users rate in the modal
      form.appendChild(clearButton);
    } else if (!mine && clear) {
      clear.parentNode.removeChild(clear);
    }
  }

  function refreshRecommendations() {
    post("/refresh", new URLSearchParams({ return_to: window.location.pathname + window.location.search }))
      .then(function (data) {
        toast(data.message || "Refreshing...", { error: data.ok === false });
        if (data.ok === false || doc.querySelector("[data-poll=recs], [data-poll=stale]")) return;
        showUpdating();
        poll("recs", {
          ready: function () { window.location.reload(); },
          error: function (message) { markUpdateFailed(message); }
        });
      }).catch(function () { toast("Couldn't reach the server - try again.", { error: true }); });
  }

  /* ---------------- colour theme picker (form[data-enhance=theme] on /appearance) ---------------- */

  var root = doc.documentElement;
  var savedTheme = root.getAttribute("data-theme");
  var themeTimer = null;
  var pickSeq = 0;  // bumped on every new selection; a response for an older one is stale

  function themeRadios() {
    return Array.prototype.slice.call(doc.querySelectorAll("form[data-enhance=theme] .theme-radio"));
  }

  function setMeta(media, color) {
    var meta = doc.querySelector('meta[name=theme-color][media="' + media + '"]');
    if (meta && color) meta.setAttribute("content", color);
  }

  /* Preview a theme at once: <html data-theme> plus the browser-chrome colour metas. No save. */
  function applyTheme(option) {
    var radio = option.querySelector(".theme-radio");
    if (!radio) return;
    root.setAttribute("data-theme", radio.value);
    setMeta("(prefers-color-scheme: dark)", option.getAttribute("data-meta-dark"));
    setMeta("(prefers-color-scheme: light)", option.getAttribute("data-meta-light"));
  }

  /* Check the radio for `key`, apply it and put the "Current" pill on it. */
  function showTheme(key, apply) {
    themeRadios().forEach(function (radio) {
      var option = radio.closest(".theme-option");
      var on = radio.value === key;
      radio.checked = on;
      var pill = option.querySelector(".theme-current");
      if (on) {
        if (apply) applyTheme(option);
        if (!pill) {
          pill = el("span", { className: "theme-current", text: "Current" });
          var name = option.querySelector(".theme-name");
          if (name && name.parentNode) name.parentNode.insertBefore(pill, name.nextSibling);
        }
      } else if (pill && pill.parentNode) {
        pill.parentNode.removeChild(pill);
      }
    });
  }

  /* Move only the "Current" pill (radios and the previewed theme stay as the user left them). */
  function markCurrent(key) {
    themeRadios().forEach(function (radio) {
      var option = radio.closest(".theme-option");
      var pill = option.querySelector(".theme-current");
      if (radio.value === key) {
        if (!pill) {
          var name = option.querySelector(".theme-name");
          if (name && name.parentNode) name.parentNode.insertBefore(el("span", { className: "theme-current", text: "Current" }), name.nextSibling);
        }
      } else if (pill && pill.parentNode) {
        pill.parentNode.removeChild(pill);
      }
    });
  }

  function saveTheme(form, keepalive) {
    clearTimeout(themeTimer);
    themeTimer = null;
    var radio = form.querySelector(".theme-radio:checked");
    if (!radio) return;
    var seq = pickSeq;
    var stale = function () { return seq !== pickSeq || themeTimer !== null; };
    var revert = function (message) {
      if (stale()) return;  // a newer pick is on screen: leave it alone
      showTheme(savedTheme, true);
      toast(message, { error: true });
    };
    post(form.getAttribute("action"), new URLSearchParams({ theme: radio.value, return_to: field(form, "return_to") }), keepalive)
      .then(function (data) {
        if (!data.ok) { revert(data.message || "Couldn't save that theme."); return; }
        savedTheme = data.theme;
        if (stale()) { markCurrent(savedTheme); return; }
        showTheme(savedTheme, false);
        toast(data.message || "Theme saved");
      }).catch(function (err) {
        if (err && err.notJson) { failed(form, err); return; }
        revert("Couldn't reach the server - try again.");
      });
  }

  doc.addEventListener("change", function (e) {
    var radio = e.target;
    if (!radio || !radio.classList || !radio.classList.contains("theme-radio")) return;
    var form = radio.form;
    if (!form || form.getAttribute("data-enhance") !== "theme") return;
    pickSeq++;
    applyTheme(radio.closest(".theme-option"));
    clearTimeout(themeTimer);
    themeTimer = setTimeout(function () { saveTheme(form); }, 400);
  });

  // Leaving within the debounce window: save now (keepalive lets the request outlive the page).
  window.addEventListener("pagehide", function () {
    var form = themeTimer !== null && doc.querySelector("form[data-enhance=theme]");
    if (form) saveTheme(form, true);
  });

  // Back/forward cache restores the old DOM: re-sync with the cookie the server set.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var match = /(?:^|;\s*)wn_theme=([^;]*)/.exec(doc.cookie || "");
    if (!match || !/^[a-z]{1,20}$/.test(match[1])) return;
    savedTheme = match[1];
    root.setAttribute("data-theme", savedTheme);
    if (themeRadios().length) {
      showTheme(savedTheme, true);
    }
  });

  var handlers = {
    theme: function (form) { saveTheme(form); },

    dismiss: function (form) {
      var type = field(form, "type"), id = field(form, "id"), returnTo = field(form, "return_to");
      var cards = findCards(type, id);
      closeModal();
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't hide that.", { error: true }); return; }
        var undo = data.undo || { type: type, id: id };
        (cards.length ? removeCards(cards) : Promise.resolve(null)).then(function (places) {
          toast(data.message || "Hidden", {
            action: { label: "Undo", run: function () { undismiss(undo, returnTo, cards, places); } }
          });
        });
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    },

    undismiss: function (form) {  // the no-JS "Removed. [Undo]" note, clicked with JS available
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't undo that.", { error: true }); return; }
        window.location.href = field(form, "return_to") || window.location.pathname;  // drops the undo params
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    },

    add: function (form) {
      var type = field(form, "type"), id = field(form, "id");
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't add that.", { error: true }); return; }
        closeModal();
        toast(data.message || "Added");
        removeCards(findCards(type, id));
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    },

    refresh: function (form) {
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't refresh.", { error: true }); return; }
        toast(data.message || "Refreshing...");
        if (doc.querySelector("[data-poll=recs], [data-poll=stale]")) return;  // already polling
        var watch = function () {
          showUpdating();
          poll("recs", {
            ready: function () { window.location.reload(); },
            error: function (message) { markUpdateFailed(message); }
          });
        };
        if (data.started) { watch(); return; }
        // started=false means "already up to date" or "a build is already running": ask which.
        statusOnce().then(function (status) { if (status && status.state === "building") watch(); });
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    },

    rate: function (form, submitter) {
      var button = submitter && submitter.name === "stars" ? submitter : form._lastStar;
      if (!button) { toast("Couldn't tell which star you picked - try again.", { error: true }); return; }
      var stars = button.value;
      // form.submit() leaves out the clicked button, so carry its value in a hidden field.
      var nativeRate = function () {
        form.appendChild(el("input", { type: "hidden", name: "stars", value: String(stars) }));
        nativeSubmit(form);
      };
      var body = new URLSearchParams(new FormData(form));
      body.set("stars", stars);
      setBusy(form, true);
      post(form.getAttribute("action"), body).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't save that rating.", { error: true }); return; }
        var rating = data.rating || {};
        applyRating(form, rating.stars, rating.plex_stars);
        // The same title can have several rate forms (hero, rows, preview): keep them in step.
        Array.prototype.forEach.call(doc.querySelectorAll("form[data-enhance=rate]"), function (other) {
          if (other !== form && field(other, "type") === field(form, "type") && field(other, "id") === field(form, "id")) {
            applyRating(other, rating.stars, rating.plex_stars);
          }
        });
        var target = stars === "0" ? form.querySelector(".star") : form.querySelector('.star[value="' + stars + '"]');
        if (target && !form.classList.contains("preview-rate")) target.focus();
        toast(data.message || "Rating saved", { action: { label: "Update recommendations", run: refreshRecommendations } });
      }).catch(function (err) {
        setBusy(form, false);
        if (err && err.notJson) { nativeRate(); return; }
        failed(form, err);
      });
    },

    generate: function (form) {
      var button = form.querySelector("button");
      var label = button ? button.textContent : "";
      setBusy(form, true);
      postForm(form).then(function (data) {
        if (!data.ok) {
          setBusy(form, false);
          toast(data.message || "Couldn't start the AI request.", { error: true });
          return;
        }
        toast(data.message || "Generating...");
        if (button) { button.disabled = true; button.textContent = "Generating..."; }
        var screen = showGeneratingScreen();
        poll("ai", {
          ready: function () { window.location.reload(); },
          error: function (message) {
            if (screen && screen.parentNode) screen.parentNode.removeChild(screen);
            if (button) { button.disabled = false; button.textContent = label; }
            setBusy(form, false);
            toast("The AI request failed: " + (message || "unknown error"), { error: true, duration: 0 });
          }
        });
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    }
  };

  doc.addEventListener("submit", function (e) {
    var form = e.target;
    var kind = form && form.getAttribute && form.getAttribute("data-enhance");
    if (!kind || !handlers[kind]) return;
    e.preventDefault();
    if (form.hasAttribute("data-busy")) return;
    handlers[kind](form, e.submitter);
  });

  // Old Safari has no e.submitter: remember which star was last pressed in a rate form.
  doc.addEventListener("click", function (e) {
    var button = e.target && e.target.closest ? e.target.closest("button[name=stars]") : null;
    if (button && button.form) button.form._lastStar = button;
  });

  // A poster that fails to load (Plex down, thumb gone) becomes the tinted placeholder.
  doc.addEventListener("error", function (e) {
    var img = e.target;
    if (img && img.tagName === "IMG" && img.parentNode &&
        (img.classList.contains("hero-backdrop") || img.classList.contains("hero-poster"))) {
      img.parentNode.removeChild(img);  // the tinted gradient behind it stays
      return;
    }
    if (!img || img.tagName !== "IMG" || !img.classList.contains("poster") || !img.parentNode) return;
    var title = img.closest("article") && img.closest("article").querySelector(".title");
    var box = el("div", { className: "poster poster-empty", "aria-hidden": "true", text: title ? title.textContent : "" });
    box.style.setProperty("--h", String(((title ? title.textContent.length : 0) * 47) % 360));
    img.parentNode.replaceChild(box, img);
  }, true);

  /* ---------------- browse pages: hero, row arrows, hover preview, top bar ---------------- */

  var mqDesktop = window.matchMedia ? window.matchMedia("(min-width: 821px)") : { matches: true };

  function onMedia(query, fn) {
    if (!query.addEventListener && !query.addListener) return;
    if (query.addEventListener) query.addEventListener("change", fn); else query.addListener(fn);
  }

  function initHero(root) {
    var slidesEl = root.querySelector("[data-hero-slides]");
    if (!slidesEl) return;
    var HERO_MS = 6000;
    var cur = 0, timer = null, remaining = HERO_MS, startedAt = 0;
    var userPaused = false, hovering = false, focusInside = false;
    var override = false;  // set when Play is pressed: the pointer/focus are still on the hero, but rotation resumes anyway
    var dotsEl = null, pauseBtn = null;

    function slides() { return Array.prototype.slice.call(slidesEl.querySelectorAll("[data-slide]")); }
    function auto() { return mqDesktop.matches && finePointer() && !reduceMotion; }
    function canRun() { return auto() && !userPaused && (override || (!hovering && !focusInside)) && !doc.hidden; }
    function clearOverride() { if (!hovering && !focusInside) override = false; }

    function restartFill() {
      var dot = dotsEl && dotsEl.querySelector(".hero-dot.on");
      if (!dot) return;
      dot.classList.remove("on");
      void dot.offsetWidth;  // restart the CSS progress animation
      dot.classList.add("on");
    }

    function stopTimer() {
      if (!timer) return;
      clearTimeout(timer);
      timer = null;
      remaining = Math.max(300, remaining - (Date.now() - startedAt));
    }

    function schedule() {
      if (timer) { clearTimeout(timer); timer = null; }
      root.classList.toggle("is-paused", auto() && !canRun());
      if (!canRun()) return;
      startedAt = Date.now();
      timer = setTimeout(function () { timer = null; show(cur + 1, true); }, remaining);
    }

    function pauseChanged() {
      if (!canRun()) { stopTimer(); root.classList.toggle("is-paused", auto()); return; }
      if (timer) root.classList.remove("is-paused");  // already running: leave the timer (and its elapsed time) alone
      else schedule();
    }

    function mark() {
      var list = slides();
      list.forEach(function (slide, i) {
        slide.classList.toggle("on", i === cur);
        if (mqDesktop.matches && i !== cur) { slide.setAttribute("aria-hidden", "true"); slide.setAttribute("inert", ""); }
        else { slide.removeAttribute("aria-hidden"); slide.removeAttribute("inert"); }
      });
      if (dotsEl) {
        Array.prototype.forEach.call(dotsEl.querySelectorAll(".hero-dot"), function (dot, i) {
          dot.classList.toggle("on", i === cur);
          if (i === cur) dot.setAttribute("aria-current", "true"); else dot.removeAttribute("aria-current");
        });
      }
    }

    function slideStep(list) {
      return list.length > 1 ? Math.abs(list[1].getBoundingClientRect().left - list[0].getBoundingClientRect().left) : slidesEl.clientWidth;
    }

    function show(n, fromTimer) {
      var list = slides();
      if (!list.length) return;
      cur = (n + list.length) % list.length;
      remaining = HERO_MS;
      if (mqDesktop.matches) {
        mark();
        restartFill();
      } else {
        var left = slidesEl.scrollLeft + list[cur].getBoundingClientRect().left - slidesEl.getBoundingClientRect().left;
        slidesEl.scrollTo({ left: left, behavior: reduceMotion ? "auto" : "smooth" });
        mark();
      }
      schedule();
    }

    function build() {
      var list = slides();
      if (dotsEl && dotsEl.parentNode) dotsEl.parentNode.removeChild(dotsEl);
      if (pauseBtn && pauseBtn.parentNode) pauseBtn.parentNode.removeChild(pauseBtn);
      dotsEl = pauseBtn = null;
      if (!list.length) return;
      list.forEach(function (slide) { slide.hidden = false; });
      dotsEl = el("div", { className: "hero-dots", role: "group", "aria-label": "Choose a featured title" });
      list.forEach(function (slide, i) {
        var title = slide.querySelector(".hero-title");
        var dot = el("button", { type: "button", className: "hero-dot", "aria-label": "Show " + (title ? title.textContent : "title " + (i + 1)) });
        dot.addEventListener("click", function () { show(i); });
        dotsEl.appendChild(dot);
      });
      root.appendChild(dotsEl);
      if (auto()) {
        pauseBtn = el("button", { type: "button", className: "hero-pause", "aria-pressed": userPaused ? "true" : "false",
                                  "aria-label": userPaused ? "Play slideshow" : "Pause slideshow", text: userPaused ? "Play" : "Pause" });
        pauseBtn.addEventListener("click", function () {
          userPaused = !userPaused;
          override = !userPaused;
          pauseBtn.setAttribute("aria-pressed", userPaused ? "true" : "false");
          pauseBtn.setAttribute("aria-label", userPaused ? "Play slideshow" : "Pause slideshow");
          pauseBtn.textContent = userPaused ? "Play" : "Pause";
          pauseChanged();
        });
        root.appendChild(pauseBtn);
      }
    }

    function sync() {
      var list = slides();
      if (!list.length) { stopTimer(); root.hidden = true; return; }
      root.hidden = false;
      if (cur >= list.length) cur = list.length - 1;
      stopTimer();
      remaining = HERO_MS;
      build();
      mark();
      if (!mqDesktop.matches) {  // phone: the slides scroll natively; start where the user is
        slidesEl.scrollLeft = 0;
        cur = 0;
        mark();
      }
      schedule();
    }

    root.addEventListener("mouseenter", function () { hovering = true; pauseChanged(); });
    root.addEventListener("mouseleave", function () { hovering = false; clearOverride(); pauseChanged(); });
    // Play's override only covers the hero controls (Pause/dots); inside a slide, hover/focus pause as normal.
    function inSlide(node) { return !!(node && node.closest && node.closest("[data-slide]")); }
    root.addEventListener("mouseover", function (e) { if (override && inSlide(e.target)) { override = false; pauseChanged(); } });
    root.addEventListener("focusin", function (e) {
      focusInside = true;
      if (inSlide(e.target)) override = false;
      pauseChanged();
    });
    root.addEventListener("focusout", function (e) {
      if (e.relatedTarget && root.contains(e.relatedTarget)) return;
      focusInside = false;
      clearOverride();
      pauseChanged();
    });
    doc.addEventListener("visibilitychange", pauseChanged);
    onMedia(mqDesktop, function () { sync(); });

    var scrollTick = null;
    slidesEl.addEventListener("scroll", function () {  // phone: dots follow the swipe
      if (mqDesktop.matches || scrollTick) return;
      scrollTick = setTimeout(function () {
        scrollTick = null;
        var list = slides();
        var step = slideStep(list) || 1;
        var index = Math.max(0, Math.min(list.length - 1, Math.round(slidesEl.scrollLeft / step)));
        if (index !== cur) { cur = index; mark(); }
      }, 60);
    }, { passive: true });

    heroApi = { sync: sync };
    sync();
  }

  var heroRoot = doc.querySelector("[data-hero]");
  if (heroRoot) initHero(heroRoot);

  /* Row arrows (mouse only): scroll a row by most of its width, hidden at either end. */
  function updateArrows(track) {
    var wrap = track.parentNode;
    var prev = wrap && wrap.querySelector(".track-arrow.prev"), next = wrap && wrap.querySelector(".track-arrow.next");
    if (!prev || !next) return;
    prev.hidden = track.scrollLeft <= 2;
    next.hidden = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
  }

  if (finePointer()) {
    Array.prototype.forEach.call(doc.querySelectorAll("[data-track]"), function (track) {
      function arrow(dir) {
        var button = el("button", { type: "button", className: "track-arrow " + dir, tabindex: "-1", "aria-hidden": "true",
                                    "aria-label": dir === "prev" ? "Scroll left" : "Scroll right" });
        button.addEventListener("click", function () {
          track.scrollBy({ left: (dir === "prev" ? -1 : 1) * track.clientWidth * 0.85, behavior: reduceMotion ? "auto" : "smooth" });
        });
        return button;
      }
      track.parentNode.appendChild(arrow("prev"));
      track.parentNode.appendChild(arrow("next"));
      track.addEventListener("scroll", function () { updateArrows(track); }, { passive: true });
      updateArrows(track);
    });
    window.addEventListener("resize", function () {
      Array.prototype.forEach.call(doc.querySelectorAll("[data-track]"), updateArrows);
    });
  }

  /* Hover preview: one shared element, a mouse-only shortcut to the card's actions. */
  var preview = null, previewCard = null, lastPreviewCard = null, openTimer = null, closeTimer = null;

  function closePreview() {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    openTimer = closeTimer = null;
    if (preview) preview.classList.remove("is-open");
    previewCard = null;
  }

  function scheduleClose() {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    closeTimer = setTimeout(closePreview, 150);
  }

  function previewButton(node, label, primary) {
    node.classList.add("preview-btn");
    if (primary) node.classList.add("primary");
    node.setAttribute("tabindex", "-1");
    node.setAttribute("aria-label", label);
    return node;
  }

  function openPreview(card) {
    if (!doc.contains(card) || (modal && modal.open) || !finePointer()) return;
    if (!preview) {
      preview = el("div", { className: "preview", "aria-hidden": "true" });
      preview.addEventListener("mouseenter", function () { clearTimeout(closeTimer); closeTimer = null; });
      preview.addEventListener("mouseleave", function (e) {
        if (!(previewCard && e.relatedTarget && previewCard.contains(e.relatedTarget))) scheduleClose();
      });
      preview.addEventListener("click", function (e) {
        var hit = e.target.closest && e.target.closest("a, button");
        if (hit && !hit.classList.contains("star")) closePreview();  // rating keeps the preview open; the content stays for the delegated handlers
      });
      doc.body.appendChild(preview);
    }
    clearTimeout(closeTimer);
    closeTimer = null;
    previewCard = lastPreviewCard = card;
    preview.textContent = "";
    var art = card.querySelector(".card-poster .poster");
    if (art) {
      var posterBox = el("div", { className: "preview-poster" }, [art.cloneNode(true)]);
      posterBox.addEventListener("click", function () { openDetail(card, card.querySelector("[data-open-detail]")); });
      preview.appendChild(posterBox);
    }

    var actions = el("div", { className: "preview-actions" });
    var add = card.querySelector("a[data-add-dialog]");
    if (add) {
      var addCopy = previewButton(add.cloneNode(true), "Add to library", true);
      addCopy.classList.remove("btn-add");
      addCopy.setAttribute("title", "Add to library");
      addCopy.textContent = "\uFF0B";
      actions.appendChild(addCopy);
    }
    var dismissForm = card.querySelector("form[data-enhance=dismiss]");
    if (dismissForm) {
      var formCopy = dismissForm.cloneNode(true);
      var formButton = formCopy.querySelector("button");
      if (formButton) {
        previewButton(formButton, "Not interested", false);
        formButton.setAttribute("title", "Not interested");
        formButton.textContent = "\u2715";
      }
      actions.appendChild(formCopy);
    }
    var rateForm = card.querySelector("form[data-enhance=rate]");
    if (rateForm) {
      var rateCopy = rateForm.cloneNode(true);
      rateCopy.classList.add("preview-rate");
      Array.prototype.forEach.call(rateCopy.querySelectorAll("button"), function (b) { b.setAttribute("tabindex", "-1"); });
      var rateText = rateCopy.querySelector(".rating-text");
      if (rateText) rateText.parentNode.removeChild(rateText);
      actions.appendChild(rateCopy);
    }
    var more = previewButton(el("button", { type: "button", title: "More info", text: "\u2304" }), "More info", false);
    more.addEventListener("click", function () {
      openDetail(card, card.querySelector("[data-open-detail]"));
    });
    actions.appendChild(more);

    var body = el("div", { className: "preview-body" }, [actions]);
    [".card-meta", ".chips", ".reason"].forEach(function (selector) {
      var source = card.querySelector(selector);
      if (source) body.appendChild(source.cloneNode(true));
    });
    preview.appendChild(body);

    var rect = card.getBoundingClientRect();
    var width = preview.offsetWidth || 320, height = preview.offsetHeight || 300;
    var left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.left + rect.width / 2 - width / 2));
    var top = Math.max(8, Math.min(window.innerHeight - height - 8, rect.top - 12));
    preview.style.left = left + "px";
    preview.style.top = top + "px";
    preview.classList.add("is-open");
  }

  if (finePointer()) {
    doc.addEventListener("mouseover", function (e) {
      var card = e.target.closest ? e.target.closest(".row-card[data-card]") : null;
      if (!card || card.closest("dialog") || card.classList.contains("is-leaving")) return;
      clearTimeout(closeTimer);
      closeTimer = null;
      if (card === previewCard) return;
      clearTimeout(openTimer);
      openTimer = setTimeout(function () { openPreview(card); }, 400);
    });
    doc.addEventListener("mouseout", function (e) {
      var card = e.target.closest ? e.target.closest(".row-card[data-card]") : null;
      if (!card || card.contains(e.relatedTarget) || (preview && e.relatedTarget && preview.contains(e.relatedTarget))) return;
      scheduleClose();
    });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape") closePreview(); });
    doc.addEventListener("scroll", function () { if (previewCard || openTimer) closePreview(); }, true);
  }

  /* Cinematic top bar: transparent over the hero, solid once the page scrolls. */
  var topbar = doc.querySelector(".topbar");
  if (topbar && doc.body.classList.contains("cinematic")) {
    var syncTopbar = function () { topbar.classList.toggle("is-scrolled", window.scrollY > 10); };
    window.addEventListener("scroll", syncTopbar, { passive: true });
    syncTopbar();
  }

  /* ---------------- status polling ---------------- */

  /* kind "recs" watches the main build, "ai" the AI batch. Calls on.ready() or on.error(message)
   * once the build stops, then stops itself. Network hiccups back off and keep trying. */
  function fetchStatus() {
    return fetch("/api/status", { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      });
  }

  function statusOnce() {
    return fetchStatus().catch(function () { return null; });
  }

  function poll(kind, on) {
    var misses = 0;
    function schedule(ms) { setTimeout(tick, ms); }
    function tick() {
      if (doc.hidden) { schedule(POLL_MS); return; }
      fetchStatus()
        .then(function (status) {
          misses = 0;
          var state = kind === "ai" ? (status.ai || {}).state : status.state;
          var error = kind === "ai" ? (status.ai || {}).error : status.error;
          if (state === "building") return schedule(POLL_MS);
          // For "recs", error stays set (with state "ready") when a rebuild failed but an older list exists.
          if (state === "error" || error) return on.error(error);
          return on.ready();
        })
        .catch(function () {
          misses += 1;
          schedule(Math.min(POLL_MS * (misses + 1), 15000));
        });
    }
    schedule(POLL_MS);
  }

  function showUpdating() {
    if (doc.querySelector("[data-poll=stale]")) return;
    var sub = doc.querySelector(".top .sub");
    if (!sub) return;
    sub.appendChild(el("span", { className: "updating", "data-poll": "stale-js" }, [
      el("span", { className: "spinner sm", "aria-hidden": "true" }),
      doc.createTextNode("Updating...")
    ]));
  }

  function markUpdateFailed(message) {
    // Calm: the older list is still on screen and still usable.
    var badge = doc.querySelector("[data-poll=stale], [data-poll=stale-js]");
    if (badge) badge.parentNode.removeChild(badge);
    toast("Couldn't refresh (" + (message || "unknown error") + ") - still showing your last list.", { duration: 8000 });
  }

  function showGeneratingScreen() {
    var main = doc.getElementById("main");
    var top = main && main.querySelector(".top");
    if (!top) return null;
    var screen = el("section", { className: "building", "aria-busy": "true" }, [
      el("div", { className: "spinner", "aria-hidden": "true" }),
      el("h3", { text: "Asking your AI for ideas..." }),
      el("p", { text: "This usually takes under a minute. This page updates itself when they're ready." })
    ]);
    top.parentNode.insertBefore(screen, top.nextSibling);
    return screen;
  }

  function showBuildError(screen, message) {
    var spinner = screen.querySelector(".spinner");
    if (spinner) spinner.parentNode.removeChild(spinner);
    screen.removeAttribute("aria-busy");
    var slot = screen.querySelector("[data-poll-error]");
    if (slot) { slot.textContent = message || "Something went wrong."; slot.hidden = false; }
    var retry = el("button", { type: "button", className: "btn-add", text: "Try again" });
    retry.addEventListener("click", function () { window.location.reload(); });
    screen.appendChild(retry);
  }

  // Building screens rendered by the server ("Finding your recommendations..." / AI generating).
  Array.prototype.forEach.call(doc.querySelectorAll("[data-poll=recs], [data-poll=ai]"), function (screen) {
    poll(screen.getAttribute("data-poll"), {
      ready: function () { window.location.reload(); },
      error: function (message) { showBuildError(screen, message); }
    });
  });

  // A stale list is on screen while a rebuild runs: offer the new one rather than yanking the page.
  var stale = doc.querySelector("[data-poll=stale]");
  if (stale) {
    poll("recs", {
      ready: function () {
        stale.textContent = "Updated";
        toast("New recommendations are ready.", {
          duration: 0, action: { label: "Show", run: function () { window.location.reload(); } }
        });
      },
      error: function (message) { markUpdateFailed(message); }
    });
  }
})();
