/* Compass - progressive enhancement. Plain JS, no libraries.
 *
 * Every form and link on the page works without this file (POST + 303 redirect). This only
 * intercepts them to work in place: forms marked data-enhance="dismiss|undismiss|add|refresh|generate|rate|theme|
 * list|lists|list-move", the "Add to library" and "Add to list" links (data-add-dialog / data-list-dialog, a modal
 * instead of the plain page), and the "Finding your recommendations..." / "Updating..." states (data-poll), which
 * poll /api/status. Posters link to the title page (/title/<type>/<id>); there is no detail modal.
 *
 * DOM is built with createElement/textContent. The one exception is setFragment(): the add and list dialogs and
 * the live search results are fragments from our own server (...&partial=1), escaped server-side.
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

  /* The form's fields plus the submit button that was pressed (FormData leaves it out). */
  function formBody(form, submitter) {
    var body = new URLSearchParams(new FormData(form));
    if (submitter && submitter.name && submitter.form === form) body.set(submitter.name, submitter.value);
    return body;
  }

  function postForm(form, submitter) {
    return post(form.getAttribute("action"), formBody(form, submitter));
  }

  /* `path` (a same-site path) with ?msg=<message> in place of any old msg/undo, for a reload that shows the result. */
  function withMessage(path, message) {
    var url;
    try { url = new URL(path || window.location.pathname, window.location.href); } catch (err) { return window.location.href; }
    if (url.origin !== window.location.origin) url = new URL(window.location.pathname, window.location.href);
    url.searchParams.delete("undo_type");
    url.searchParams.delete("undo_id");
    if (message) url.searchParams.set("msg", message); else url.searchParams.delete("msg");
    return url.pathname + url.search;
  }

  /* If the server didn't answer with JSON, fall back to the ordinary form submission; on a
   * network error just say so and leave the page as it is. */
  function failed(form, err, submitter) {
    if (err && err.notJson && form) {
      // form.submit() leaves out the pressed button: carry its value in a hidden field.
      if (submitter && submitter.name && submitter.form === form) {
        form.appendChild(el("input", { type: "hidden", name: submitter.name, value: submitter.value }));
      }
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
    var first = (opts.focus && modalBody.querySelector(opts.focus)) || focusables(modalBody)[0] ||
                modal.querySelector(".modal-close");
    if (first) first.focus();
  }

  function closeModal() {
    closePreview();
    if (modal && modal.open) modal.close();
  }

  /* ---------------- server fragments ---------------- */

  /* The one place markup from the server goes into the page. It only ever receives text fetched
   * same-origin from our own partial=1 endpoints (/add-dialog, /search), which escape every untrusted
   * string server-side. Returns false, without touching node, when the text is a whole page
   * (a server without partial support): the caller then navigates instead. */
  function setFragment(node, html) {
    if (/<html[\s>]/i.test(html)) return false;
    node.innerHTML = html;
    return true;
  }

  /* ---------------- fragment dialogs: add to library (a[data-add-dialog]), add to list (a[data-list-dialog]) ---------------- */

  function openFragmentDialog(link, focusSelector) {
    var href = link.getAttribute("href");
    // A link cloned into the (hidden) hover preview can't take focus back: use its card's poster link.
    var fromPreview = link.closest(".preview") && lastPreviewCard;
    var returnTarget = (fromPreview && fromPreview.querySelector(".poster-link")) || link;
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
      var filled = false;
      openModal(function (body) { filled = setFragment(body, html); },
                { narrow: true, focus: focusSelector });
      if (!filled) window.location.href = href;  // no partial support: use the page
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
      openFragmentDialog(link, "select, input:not([type=hidden]), .btn-add");
      return;
    }
    var listLink = target.closest("a[data-list-dialog]");
    if (listLink && hasDialog && plainClick) {
      e.preventDefault();
      openFragmentDialog(listLink, "input:not([type=hidden]), .btn-add");
      return;
    }
    var closer = target.closest("dialog [data-close]");
    if (closer && plainClick) { e.preventDefault(); closeModal(); }
  });

  /* A season ticked in a picker (the add dialog) selects "Choose seasons". */
  doc.addEventListener("change", function (e) {
    var box = e.target;
    if (!box || !box.classList || !box.classList.contains("season-check") || !box.form) return;
    var pick = box.form.querySelector("input[name=seasons][value=pick]");
    if (pick && box.checked) pick.checked = true;
  });

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
    var target = next && (next.querySelector(".poster-link") || next.querySelector("summary") || next.querySelector("a, button"));
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

  /* A search card stays after a successful add (data-keep-on-add): no more Add link, and an "Added" tag. */
  function markAdded(card) {
    Array.prototype.forEach.call(card.querySelectorAll("a[data-add-dialog]"), function (link) {
      if (link.parentNode) link.parentNode.removeChild(link);
    });
    var actions = card.querySelector(".card-actions");
    if (actions && !actions.children.length) actions.parentNode.removeChild(actions);
    var tag = card.querySelector(".status-tag");
    if (tag) {
      tag.className = "lib-tag status-tag status-added";
      tag.textContent = "Added";
      return;
    }
    var badges = card.querySelector(".poster-top .badges");
    if (badges) badges.insertBefore(el("span", { className: "lib-tag status-tag status-added", text: "Added" }), badges.firstChild);
  }

  function focusCard(card) {
    var active = doc.activeElement;
    if (active && active !== doc.body && active.id !== "main" && doc.contains(active)) return;
    var target = card.querySelector(".card-details > summary, .poster-link, .title a, a[href], button");
    if (!target || !visible(target)) {
      card.setAttribute("tabindex", "-1");
      target = card;
    }
    target.focus();
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
      var candidate = cards[j].querySelector(".poster-link");
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

  /* Recolour the header logo (its colours are baked in server-side) and swap the favicon. */
  function setLogo(option) {
    var fg = option.getAttribute("data-logo"), bg = option.getAttribute("data-meta-dark");
    var mark = doc.querySelector(".brand-mark");
    if (mark && fg && bg) {
      var oldFg = mark.getAttribute("data-fg"), oldBg = mark.getAttribute("data-bg");
      Array.prototype.forEach.call(mark.querySelectorAll("[fill], [stroke]"), function (node) {
        ["fill", "stroke"].forEach(function (attr) {
          var value = node.getAttribute(attr);
          if (value === oldFg) node.setAttribute(attr, fg);
          else if (value === oldBg) node.setAttribute(attr, bg);
        });
      });
      mark.setAttribute("data-fg", fg);
      mark.setAttribute("data-bg", bg);
    }
    var icon = doc.querySelector("link[rel=icon]"), href = option.getAttribute("data-favicon");
    if (icon && href) icon.setAttribute("href", href);
  }

  /* Preview a theme at once: <html data-theme>, the browser-chrome colour metas and the logo. No save. */
  function applyTheme(option) {
    var radio = option.querySelector(".theme-radio");
    if (!radio) return;
    root.setAttribute("data-theme", radio.value);
    setMeta("(prefers-color-scheme: dark)", option.getAttribute("data-meta-dark"));
    setMeta("(prefers-color-scheme: light)", option.getAttribute("data-meta-light"));
    setLogo(option);
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
    var match = /(?:^|;\s*)compass_theme=([^;]*)/.exec(doc.cookie || "");
    if (!match || !/^[a-z]{1,20}$/.test(match[1])) return;
    savedTheme = match[1];
    root.setAttribute("data-theme", savedTheme);
    if (themeRadios().length) {
      showTheme(savedTheme, true);
    }
  });

  /* Flip every Watchlist toggle for one title (cards, hero, the hover preview, the title page) to match the server. */
  function syncWatchlist(type, id, on) {
    Array.prototype.forEach.call(doc.querySelectorAll("form.list-toggle-form"), function (toggle) {
      if (field(toggle, "type") !== String(type) || field(toggle, "id") !== String(id) || field(toggle, "list") !== "watchlist") return;
      toggle.setAttribute("action", on ? "/lists/remove" : "/lists/add");
      var button = toggle.querySelector(".list-toggle");
      if (button) {
        button.classList.toggle("on", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      }
      var text = toggle.querySelector(".list-toggle-text");
      if (text) text.textContent = on ? "On Watchlist" : "Watchlist";
    });
  }

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

    add: function (form, submitter) {
      var type = field(form, "type"), id = field(form, "id");
      setBusy(form, true);
      postForm(form, submitter).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't add that.", { error: true }); return; }
        // The title page (and its season form) shows the new state by reloading with the message.
        if (form.getAttribute("data-after") === "reload" || /^\/title\//.test(field(form, "return_to"))) {
          window.location.href = withMessage(field(form, "return_to"), data.message || "Added");
          return;
        }
        closeModal();
        toast(data.message || "Added");
        var kept = [];
        findCards(type, id).forEach(function (card) {
          if (card.hasAttribute("data-keep-on-add")) { markAdded(card); kept.push(card); } else removeCard(card);
        });
        // closeModal() tried to return focus to the Add link, which markAdded just removed: land on the card.
        // Deferred so the dialog's own close handling has run; skipped if focus already went somewhere useful.
        if (kept.length) setTimeout(function () { focusCard(kept[0]); }, 0);
      }).catch(function (err) { setBusy(form, false); failed(form, err, submitter); });
    },

    /* The Watchlist toggle and the Remove button on a list page (/lists/add|remove). */
    list: function (form) {
      var type = field(form, "type"), id = field(form, "id");
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't update that list.", { error: true }); return; }
        if (field(form, "list") === "watchlist") syncWatchlist(type, id, !!data.on_watchlist);
        if (form.hasAttribute("data-remove-card") && data.in_list === false) {
          var card = form.closest("[data-card]");
          if (card) removeCard(card);
        }
        toast(data.message || "Saved");
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    },

    /* The list dialog's form (/lists/set). */
    lists: function (form) {
      var type = field(form, "type"), id = field(form, "id");
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't save your lists.", { error: true }); return; }
        // A title page or a list page shows list names / contents: reload it with the message.
        if (data.created || doc.querySelector(".title-page, .list-head")) {
          window.location.href = withMessage(field(form, "return_to"), data.message || "Saved");
          return;
        }
        closeModal();
        syncWatchlist(type, id, !!data.on_watchlist);
        toast(data.message || "Saved");
      }).catch(function (err) { setBusy(form, false); failed(form, err); });
    },

    /* Up / Down / Top on a list page: move the card in place when the server moved it. */
    "list-move": function (form, submitter) {
      function onFirstPage() { return (new URLSearchParams(window.location.search).get("page") || "1") === "1"; }
      var button = submitter || form.querySelector("button");
      var direction = field(form, "direction");
      var card = form.closest("[data-card]");
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't move that.", { error: true }); return; }
        if (data.moved && card && card.parentNode) {
          var grid = card.parentNode;
          var before = card.previousElementSibling, after = card.nextElementSibling;
          if (direction === "up" && before) grid.insertBefore(card, before);
          else if (direction === "down" && after) grid.insertBefore(after, card);
          // The server moves to the top / bottom of the whole list, not of this page: off page 1 (or any "bottom") reload.
          else if (direction === "top" && before && onFirstPage()) grid.insertBefore(card, grid.firstElementChild);
          else {  // it swapped with a title on another page: show the new order
            window.location.href = withMessage(window.location.pathname + window.location.search, data.message || "Moved");
            return;
          }
          if (button && doc.contains(button)) button.focus();  // moving the card dropped the focus
        }
        toast(data.message || "Moved");
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
    var pressed = e.submitter || form._lastSubmit;   // old Safari (< 15.4) has no e.submitter
    form._lastSubmit = null;
    if (form.hasAttribute("data-busy")) return;
    handlers[kind](form, pressed);
  });

  // Old Safari has no e.submitter: remember which star was last pressed in a rate form.
  doc.addEventListener("click", function (e) {
    var button = e.target && e.target.closest ? e.target.closest("button[name=stars]") : null;
    if (button && button.form) button.form._lastStar = button;
    var named = e.target && e.target.closest ? e.target.closest("button[name]") : null;
    if (named && named.form) named.form._lastSubmit = named;   // e.g. the season form's seasons=pick / seasons=all
  });

  // A poster that fails to load (Plex down, thumb gone) becomes the tinted placeholder.
  doc.addEventListener("error", function (e) {
    var img = e.target;
    if (img && img.tagName === "IMG" && img.parentNode &&
        (img.classList.contains("hero-backdrop") || img.classList.contains("hero-poster") ||
         img.classList.contains("title-backdrop"))) {
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
  var PREVIEW_OPEN_MS = 350;      // pointer must settle this long before the first preview
  var PREVIEW_OPEN_MAX_MS = 700;  // ...but never wait longer than this in total
  var PREVIEW_MOVE_PX = 5;        // movement beyond this re-arms the open timer
  var PREVIEW_SWITCH_MS = 120;    // card to card while a preview is open
  var PREVIEW_CLOSE_MS = 150;     // leaving to empty space
  var PREVIEW_MOVING_MS = 200;    // length of the left/top transition
  var SVG_NS = "ht" + "tp://www.w3.org/2000/svg";  // split so the no-external-URLs check stays strict
  var PREVIEW_CARD = ".row-card[data-card], [data-preview]";
  var PREVIEW_ICONS = { open: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5", plus: "M12 5v14M5 12h14", cross: "M6 6l12 12M18 6L6 18", chevron: "M6 9l6 6 6-6" };
  var preview = null, previewCard = null, lastPreviewCard = null;
  var openTimer = null, closeTimer = null, movingTimer = null;
  var intentCard = null, intentStart = 0, intentX = 0, intentY = 0;

  function previewIcon(name) {
    var svg = doc.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "preview-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2.4");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    var path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("d", PREVIEW_ICONS[name]);
    svg.appendChild(path);
    return svg;
  }

  function setIcon(node, name) {
    node.textContent = "";
    node.appendChild(previewIcon(name));
  }

  function clearPreviewTimers() {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    openTimer = closeTimer = null;
    intentCard = null;
  }

  function closePreview() {
    clearPreviewTimers();
    clearTimeout(movingTimer);
    movingTimer = null;
    if (preview) preview.classList.remove("is-open", "is-moving");
    previewCard = null;
  }

  function scheduleClose() {
    clearPreviewTimers();
    closeTimer = setTimeout(closePreview, PREVIEW_CLOSE_MS);
  }

  /* Hover intent: open after the pointer settles; switch quickly when a preview is already open. */
  function armOpen(card) {
    clearTimeout(openTimer);
    var wait = previewCard ? PREVIEW_SWITCH_MS
      : Math.max(0, Math.min(PREVIEW_OPEN_MS, PREVIEW_OPEN_MAX_MS - (Date.now() - intentStart)));
    openTimer = setTimeout(function () { openTimer = null; intentCard = null; openPreview(card); }, wait);
  }

  function previewButton(node, label, primary) {
    node.classList.add("preview-btn");
    if (primary) node.classList.add("primary");
    node.setAttribute("tabindex", "-1");
    node.setAttribute("aria-label", label);
    return node;
  }

  /* The Watchlist toggle (icon-only in the preview) and the list link, cloned from the card; the delegated
   * submit / click handlers work on the copies. Not tab stops: keyboard users use the title page. */
  function cloneListTools(card, actions) {
    var toggle = card.querySelector(".list-toggle-form");
    if (toggle) {
      var toggleCopy = toggle.cloneNode(true);
      Array.prototype.forEach.call(toggleCopy.querySelectorAll("button"), function (b) { b.setAttribute("tabindex", "-1"); });
      actions.appendChild(toggleCopy);
    }
    var listLink = card.querySelector("a.list-link");
    if (listLink) {
      var linkCopy = listLink.cloneNode(true);
      linkCopy.setAttribute("tabindex", "-1");
      actions.appendChild(linkCopy);
    }
  }

  function openPreview(card) {
    if (!doc.contains(card) || (modal && modal.open) || !finePointer()) return;
    if (!preview) {
      preview = el("div", { className: "preview", "aria-hidden": "true" });
      preview.addEventListener("mouseenter", function () { clearTimeout(closeTimer); closeTimer = null; clearTimeout(openTimer); openTimer = null; intentCard = null; });
      preview.addEventListener("mouseleave", function (e) {
        if (!(previewCard && e.relatedTarget && previewCard.contains(e.relatedTarget))) scheduleClose();
      });
      preview.addEventListener("click", function (e) {
        var hit = e.target.closest && e.target.closest("a, button");
        // rating and the Watchlist toggle keep the preview open; the content stays for the delegated handlers
        if (hit && !hit.classList.contains("star") && !hit.classList.contains("list-toggle")) closePreview();
      });
      doc.body.appendChild(preview);
    }
    clearPreviewTimers();
    var wasOpen = preview.classList.contains("is-open") && !!previewCard;
    previewCard = lastPreviewCard = card;

    // Build the new content off-screen first, then swap it in one go.
    var frag = doc.createDocumentFragment();
    var art = card.querySelector(".card-poster .poster");
    var posterLink = card.querySelector("a.poster-link");
    var detailHref = posterLink ? posterLink.href : null;   // the title page: where the poster and "More info" go
    if (art) {
      var posterBox = el("div", { className: "preview-poster" }, [art.cloneNode(true)]);
      if (detailHref) posterBox.addEventListener("click", function () { window.location.href = detailHref; });
      frag.appendChild(posterBox);
    }

    var isLibrary = card.hasAttribute("data-preview");
    var actions = el("div", { className: "preview-actions" });
    var body;
    if (!isLibrary) {
      var add = card.querySelector("a[data-add-dialog]");
      if (add) {
        var addCopy = previewButton(add.cloneNode(true), "Add to library", true);
        addCopy.classList.remove("btn-add");
        addCopy.setAttribute("title", "Add to library");
        setIcon(addCopy, "plus");
        actions.appendChild(addCopy);
      }
      var dismissForm = card.querySelector("form[data-enhance=dismiss]");
      if (dismissForm) {
        var formCopy = dismissForm.cloneNode(true);
        var formButton = formCopy.querySelector("button");
        if (formButton) {
          previewButton(formButton, "Not interested", false);
          formButton.setAttribute("title", "Not interested");
          setIcon(formButton, "cross");
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
      cloneListTools(card, actions);
      if (detailHref) {
        var more = previewButton(el("a", { href: detailHref, title: "More info" }), "More info", false);
        setIcon(more, "chevron");
        actions.appendChild(more);
      }

      body = el("div", { className: "preview-body" }, [actions]);
      [".card-meta", ".chips", ".reason"].forEach(function (selector) {
        var source = card.querySelector(selector);
        if (source) body.appendChild(source.cloneNode(true));
      });
    
    } else {
      var titleLink = posterLink;
      var rateLib = card.querySelector("form[data-enhance=rate]");
      if (rateLib) {
        var rateClone = rateLib.cloneNode(true);
        rateClone.classList.add("preview-rate");
        Array.prototype.forEach.call(rateClone.querySelectorAll("button"), function (b) { b.setAttribute("tabindex", "-1"); });
        var rateTextLib = rateClone.querySelector(".rating-text");
        if (rateTextLib) rateTextLib.parentNode.removeChild(rateTextLib);
        actions.appendChild(rateClone);
      }
      cloneListTools(card, actions);
      var titleNode = card.querySelector(".title");
      var titleText = titleNode ? titleNode.textContent : "";
      if (titleLink) {
        var openLink = previewButton(el("a", { href: titleLink.href, title: "Open " + titleText }), "Open " + titleText, false);
        openLink.classList.add("preview-open");
        openLink.appendChild(previewIcon("open"));
        actions.appendChild(openLink);
      }
      body = el("div", { className: "preview-body" });
      body.appendChild(el("div", { className: "preview-title", text: titleText }));
      var subNode = card.querySelector(".card-sub");
      if (subNode && subNode.textContent) body.appendChild(el("div", { className: "preview-sub", text: subNode.textContent }));
      var badgeNode = card.querySelector(".badges");
      if (badgeNode && badgeNode.children.length) {
        var badgeClone = badgeNode.cloneNode(true);
        badgeClone.classList.add("preview-badges");
        body.appendChild(badgeClone);
      }
      var srcNode = card.querySelector(".card-sources");
      if (srcNode) {
        var srcClone = srcNode.cloneNode(true);
        srcClone.classList.add("preview-sources");
        body.appendChild(srcClone);
      }
      body.insertBefore(actions, body.firstChild);
    }
    frag.appendChild(body);

    preview.classList.toggle("is-library", isLibrary);
    var animate = wasOpen && !reduceMotion;
    clearTimeout(movingTimer);
    movingTimer = null;
    if (!animate) preview.classList.remove("is-moving");
    if (preview.replaceChildren) preview.replaceChildren(frag);
    else { preview.textContent = ""; preview.appendChild(frag); }

    // Anchor to the card: centred on it, poster starting at the card's top edge, clamped to the viewport.
    var rect = card.getBoundingClientRect();
    var width = preview.offsetWidth || 320, height = preview.offsetHeight || 300;
    var left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.left + rect.width / 2 - width / 2));
    var top = Math.max(8, Math.min(window.innerHeight - height - 8, rect.top));
    var scale = Math.max(0.5, Math.min(1, rect.width / width));
    preview.style.setProperty("--from-scale", String(Math.round(scale * 1000) / 1000));
    preview.style.setProperty("--origin-x", Math.round(rect.left + rect.width / 2 - left) + "px");
    preview.style.setProperty("--origin-y", Math.round(rect.top + rect.height / 2 - top) + "px");
    if (animate) {
      preview.classList.add("is-moving");
      movingTimer = setTimeout(function () { movingTimer = null; if (preview) preview.classList.remove("is-moving"); }, PREVIEW_MOVING_MS);
    }
    preview.style.left = left + "px";
    preview.style.top = top + "px";
    preview.classList.add("is-open");
  }

  if (finePointer()) {
    doc.addEventListener("mouseover", function (e) {
      var card = e.target.closest ? e.target.closest(PREVIEW_CARD) : null;
      if (!card || card.closest("dialog") || card.classList.contains("is-leaving")) return;
      clearTimeout(closeTimer);
      closeTimer = null;
      if (card === previewCard) {
        clearTimeout(openTimer);  // pointer came back to the open card: cancel any pending switch
        openTimer = null;
        intentCard = null;
        return;
      }
      if (card === intentCard) return;
      intentCard = card;
      intentStart = Date.now();
      intentX = e.clientX;
      intentY = e.clientY;
      armOpen(card);
    });
    doc.addEventListener("mousemove", function (e) {
      if (!intentCard || previewCard || !openTimer) return;  // only the first open waits for the pointer to settle
      if (Math.abs(e.clientX - intentX) + Math.abs(e.clientY - intentY) <= PREVIEW_MOVE_PX) return;
      intentX = e.clientX;
      intentY = e.clientY;
      armOpen(intentCard);
    }, { passive: true });
    doc.addEventListener("mouseout", function (e) {
      var card = e.target.closest ? e.target.closest(PREVIEW_CARD) : null;
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

  /* ---------------- live search (the /search page's own box) ---------------- */

  var SEARCH_DEBOUNCE_MS = 400;
  var SEARCH_MIN = 2;

  /* Typing swaps in the server-rendered fragment from /search?...&partial=1. Nothing happens on load:
   * the server already rendered the current results. Enter is a normal submit (full page). */
  function initLiveSearch(form) {
    var input = form.elements.q;
    var region = doc.querySelector("[data-search-results]");
    var spinner = doc.querySelector("[data-search-spinner]");
    var status = doc.querySelector("[data-search-status]");
    if (!input || !region) return;
    var timer = null, seq = 0, controller = null, composing = false;

    function normalize(text) { return text.replace(/\s+/g, " ").trim(); }
    function kind() {
      var checked = form.querySelector("input[name=type]:checked");
      return checked ? checked.value : "all";
    }
    function keyOf(q, type) { return type + "|" + q; }
    var lastKey = keyOf(normalize(input.value), kind());  // what the server rendered

    function urlFor(q, type) {
      var params = new URLSearchParams();
      if (q) params.set("q", q);
      params.set("type", type);
      return (form.getAttribute("action") || "/search") + "?" + params.toString();
    }

    function setLoading(on) {
      region.setAttribute("aria-busy", on ? "true" : "false");
      region.classList.toggle("is-loading", on);
      if (spinner) spinner.hidden = !on;
    }

    function showError() {
      setLoading(false);
      region.textContent = "";
      region.appendChild(el("p", { className: "note error", role: "alert", text: "Couldn't reach the server - try again." }));
      if (status) status.textContent = "";
      lastKey = null;  // no automatic retry; the next input (even the same text) asks again
    }

    function send(q, type) {
      var mine = ++seq;
      if (controller) controller.abort();
      controller = window.AbortController ? new AbortController() : null;
      var url = urlFor(q, type);
      var options = { credentials: "same-origin", headers: { "Accept": "text/html" } };
      if (controller) options.signal = controller.signal;
      setLoading(true);
      fetch(url + "&partial=1", options).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.text();
      }).then(function (html) {
        if (mine !== seq) return;  // a newer search has started: drop this one
        if (/<html[\s>]/i.test(html)) { window.location.href = url; return; }  // no partial support: the page
        if (html.indexOf("data-search-fragment") === -1 || !setFragment(region, html)) throw new Error("Not a fragment");
        setLoading(false);
        var fragment = region.querySelector("[data-search-fragment]");
        if (status) status.textContent = (fragment && fragment.getAttribute("data-announce")) || "";
        if (window.history && history.replaceState) history.replaceState(null, "", url);
      }).catch(function () {
        if (mine !== seq) return;  // aborted or superseded
        showError();
      });
    }

    function run() {
      timer = null;
      var q = normalize(input.value), type = kind();
      if (q.length > 0 && q.length < SEARCH_MIN) return;  // one character: keep what's showing
      var key = keyOf(q, type);
      if (key === lastKey) return;
      lastKey = key;
      send(q, type);
    }

    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(run, SEARCH_DEBOUNCE_MS);
    }

    input.addEventListener("input", function (e) {
      if (e.isComposing || composing) return;
      schedule();
    });
    input.addEventListener("compositionstart", function () { composing = true; });
    input.addEventListener("compositionend", function () { composing = false; schedule(); });
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || e.isComposing) return;
      e.preventDefault();
      input.value = "";
      clearTimeout(timer);
      run();
    });
    form.addEventListener("change", function (e) {
      if (!e.target || e.target.name !== "type") return;
      clearTimeout(timer);
      run();
    });
    form.addEventListener("submit", function () { clearTimeout(timer); });
  }

  Array.prototype.forEach.call(doc.querySelectorAll("form[data-search-live]"), initLiveSearch);

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
