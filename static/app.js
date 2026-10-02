/* What's Next - progressive enhancement. Plain JS, no libraries.
 *
 * Every form and link on the page works without this file (POST + 303 redirect). This only
 * intercepts them to work in place: forms marked data-enhance="dismiss|undismiss|add|refresh|generate",
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

  function post(url, body) {
    return fetch(url, {
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

  function findCard(type, id) {
    var key = type + "-" + id;
    var cards = doc.querySelectorAll("[data-card]");
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].getAttribute("data-card") === key) return cards[i];
    }
    return null;
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
    if (modal && modal.open) modal.close();
  }

  /* ---------------- card details ---------------- */

  function openDetail(card, trigger) {
    openModal(function (body) {
      var poster = card.querySelector(".card-poster");
      var posterCopy = poster ? poster.cloneNode(true) : null;
      if (posterCopy) posterCopy.removeAttribute("data-open-detail");
      var title = card.querySelector(".title");
      var main = el("div", { className: "detail-main" }, [
        el("h3", { id: "detail-title", text: title ? title.textContent : "" })
      ]);
      var detail = card.querySelector(".detail-body");
      if (detail) main.appendChild(detail.cloneNode(true));
      var actions = card.querySelector(".card-actions");
      if (actions) main.appendChild(actions.cloneNode(true));  // delegated handlers find the card by type/id
      body.appendChild(el("div", { className: "detail-layout" }, [posterCopy, main]));
    }, { returnFocus: trigger, focus: ".detail-main .btn-add, .detail-main button[type=submit]" });
  }

  /* ---------------- add-to-library dialog ---------------- */

  function openAddDialog(link) {
    var href = link.getAttribute("href");
    openModal(function (body) {
      body.appendChild(el("div", { className: "modal-loading", role: "status" }, [
        el("div", { className: "spinner", "aria-hidden": "true" }),
        el("span", { text: "Loading options..." })
      ]));
    }, { narrow: true, returnFocus: link, focus: ".modal-close" });

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
    var details = card.querySelector(".card-details");
    openDetail(card, details ? details.querySelector("summary") : null);
  });

  /* ---------------- removing / restoring cards ---------------- */

  function syncEmpty(grid) {
    if (!grid || !grid.parentNode) return;
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

  function focusNeighbour(card) {
    var next = card.nextElementSibling || card.previousElementSibling;
    var target = next && (next.querySelector("summary") || next.querySelector("a, button"));
    if (target) target.focus();
    else { var main = doc.getElementById("main"); if (main) main.focus(); }
  }

  function removeCard(card) {
    var place = { parent: card.parentNode, next: card.nextSibling };
    return animate(card, "is-leaving").then(function () {
      if (card.contains(doc.activeElement)) focusNeighbour(card);
      if (card.parentNode) card.parentNode.removeChild(card);
      card.classList.remove("is-leaving");
      syncEmpty(place.parent);
      return place;
    });
  }

  function reinsertCard(card, place) {
    if (!place || !place.parent || !doc.contains(place.parent)) { window.location.reload(); return; }
    var next = place.next && place.next.parentNode === place.parent ? place.next : null;
    place.parent.insertBefore(card, next);
    syncEmpty(place.parent);
    animate(card, "is-entering").then(function () { card.classList.remove("is-entering"); });
    var summary = card.querySelector("summary");
    if (summary) summary.focus();
  }

  function undismiss(undo, returnTo, card, place) {
    var body = new URLSearchParams();
    body.set("type", undo.type);
    body.set("id", String(undo.id));
    body.set("return_to", returnTo || window.location.pathname + window.location.search);
    post("/undismiss", body).then(function (data) {
      if (!data.ok) { toast(data.message || "Couldn't undo that.", { error: true }); return; }
      if (card && place) reinsertCard(card, place); else window.location.reload();
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
      form.appendChild(el("button", { type: "submit", name: "stars", value: "0", className: "link-btn star-clear", text: "Clear rating" }));
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

  var handlers = {
    dismiss: function (form) {
      var type = field(form, "type"), id = field(form, "id"), returnTo = field(form, "return_to");
      var card = findCard(type, id);
      closeModal();
      setBusy(form, true);
      postForm(form).then(function (data) {
        setBusy(form, false);
        if (!data.ok) { toast(data.message || "Couldn't hide that.", { error: true }); return; }
        var undo = data.undo || { type: type, id: id };
        (card ? removeCard(card) : Promise.resolve(null)).then(function (place) {
          toast(data.message || "Hidden", {
            action: { label: "Undo", run: function () { undismiss(undo, returnTo, card, place); } }
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
        var card = findCard(type, id);
        if (card) removeCard(card);
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
        var target = stars === "0" ? form.querySelector(".star") : form.querySelector('.star[value="' + stars + '"]');
        if (target) target.focus();
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
    if (!img || img.tagName !== "IMG" || !img.classList.contains("poster") || !img.parentNode) return;
    var title = img.closest("article") && img.closest("article").querySelector(".title");
    var box = el("div", { className: "poster poster-empty", "aria-hidden": "true", text: title ? title.textContent : "" });
    box.style.setProperty("--h", String(((title ? title.textContent.length : 0) * 47) % 360));
    img.parentNode.replaceChild(box, img);
  }, true);

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
