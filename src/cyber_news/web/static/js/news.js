// Progressive enhancement for the news page; without JS every control is a plain link or form.
// Load more and Show older fetch server-rendered fragments (/news, /news/older) and insert them in place.
(function () {
  "use strict";

  var liveRegion = document.getElementById("live-region");

  function announce(text) {
    if (!liveRegion) return;
    liveRegion.textContent = "";
    window.setTimeout(function () { liveRegion.textContent = text; }, 50);
  }

  function parseFragment(html) {
    var template = document.createElement("template");
    template.innerHTML = html;
    return template.content;
  }

  function setBusy(control, busy) {
    var spinner = control.querySelector(".js-spinner");
    control.classList.toggle("disabled", busy);
    if (busy) {
      control.setAttribute("aria-disabled", "true");
      control.setAttribute("aria-busy", "true");
    } else {
      control.removeAttribute("aria-disabled");
      control.removeAttribute("aria-busy");
    }
    if (spinner) spinner.classList.toggle("d-none", !busy);
  }

  function errorAlert(text) {
    var alert = document.createElement("div");
    alert.className = "alert alert-warning mb-3 fragment-error";
    alert.setAttribute("role", "alert");
    alert.textContent = text;
    return alert;
  }

  function clearErrors(container) {
    container.querySelectorAll(".fragment-error").forEach(function (node) { node.remove(); });
  }

  // Fetches the control's fragment. Resolves with the parsed fragment on 200; on 410 the reload prompt
  // replaces the control; on any other failure an alert is shown and the control can be clicked again.
  function fetchFragment(control) {
    var holder = control.parentElement;
    clearErrors(holder);
    setBusy(control, true);
    return fetch(control.dataset.fragmentUrl, { headers: { Accept: "text/html" }, credentials: "same-origin" })
      .then(function (response) {
        return response.text().then(function (html) {
          if (response.ok) return parseFragment(html);
          var fragment = parseFragment(html);
          if (response.status === 410) {
            control.replaceWith(fragment);
          } else {
            holder.insertBefore(fragment.querySelector(".fragment-error") ? fragment : errorAlert(
              "Could not load news, try again shortly."), control);
            setBusy(control, false);
          }
          announce("Could not load news");
          return null;
        });
      })
      .catch(function () {
        holder.insertBefore(errorAlert("Could not load news. Check your connection and try again."), control);
        setBusy(control, false);
        announce("Could not load news");
        return null;
      });
  }

  function withCursor(url, cursor) {
    var parsed = new URL(url, window.location.href);
    parsed.searchParams.set("cursor", cursor);
    return parsed.pathname + parsed.search;
  }

  function loadMore(control) {
    var section = control.closest(".day-section, .search-results");
    fetchFragment(control).then(function (fragment) {
      if (!fragment) return;
      var incoming = fragment.querySelector(".news-grid");
      var grid = section.querySelector(".news-grid");
      if (!incoming || !grid) {
        setBusy(control, false);
        return;
      }
      var cards = Array.prototype.slice.call(incoming.children);
      cards.forEach(function (card) { grid.appendChild(card); });
      section.querySelectorAll(".js-total").forEach(function (node) { node.textContent = incoming.dataset.total; });
      var shown = document.querySelector(".result-counter .js-shown");
      if (shown && section.classList.contains("search-results")) {
        shown.textContent = String(grid.children.length);
        var total = document.querySelector(".result-counter .js-total");
        if (total) total.textContent = incoming.dataset.total;
      } else {
        announce("Loaded " + cards.length + " more news");
      }
      var next = incoming.dataset.nextCursor;
      if (next) {
        control.href = withCursor(control.href, next);
        control.dataset.fragmentUrl = withCursor(control.dataset.fragmentUrl, next);
        setBusy(control, false);
        return;
      }
      var older = section.querySelector(".js-show-older-template");
      if (older) {
        control.replaceWith(older.content.cloneNode(true));
        older.remove();
      } else {
        control.remove();
      }
    });
  }

  function showOlder(control) {
    var sections = document.querySelector(".day-sections");
    fetchFragment(control).then(function (fragment) {
      if (!fragment) return;
      var section = fragment.querySelector(".day-section");
      if (!section) {
        control.replaceWith(fragment);
        announce("No older news");
        return;
      }
      sections.appendChild(section);
      control.remove();
      var heading = section.querySelector(".day-heading");
      if (heading) {
        heading.focus();
        announce("Showing news from " + heading.dataset.dayLabel);
      }
    });
  }

  document.addEventListener("click", function (event) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var control = event.target.closest(".js-load-more, .js-show-older");
    if (!control) return;
    event.preventDefault();
    if (control.getAttribute("aria-disabled") === "true") return; // a request is already in flight
    if (control.classList.contains("js-load-more")) {
      loadMore(control);
    } else {
      showOlder(control);
    }
  });

  // The date picker submits as soon as a complete, valid date is picked; the form never carries q.
  var dateInput = document.querySelector(".js-date-input");
  if (dateInput) {
    var dateSubmit = document.querySelector(".js-date-submit");
    if (dateSubmit) dateSubmit.classList.add("d-none");
    dateInput.addEventListener("change", function () {
      if (dateInput.value && dateInput.checkValidity()) dateInput.form.requestSubmit();
    });
  }

  // The clear (×) button empties the search box; on a results page it goes back to today's news.
  var searchInput = document.getElementById("search-input");
  var clearButton = document.querySelector(".js-clear-search");
  if (searchInput && clearButton) {
    var searching = searchInput.value.trim() !== "";
    searchInput.addEventListener("input", function () {
      clearButton.classList.toggle("d-none", searchInput.value === "" && !searching);
    });
    clearButton.addEventListener("click", function () {
      searchInput.value = "";
      if (searching) {
        window.location.assign(searchInput.form.getAttribute("action"));
      } else {
        clearButton.classList.add("d-none");
        searchInput.focus();
      }
    });
  }

  var tipsButton = document.querySelector(".js-search-tips");
  var tipsContent = document.getElementById("search-tips-content");
  if (tipsButton && tipsContent && window.bootstrap) {
    var tips = new window.bootstrap.Popover(tipsButton, {
      html: true,
      content: function () { return tipsContent.content.cloneNode(true).firstElementChild; },
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") tips.hide();
    });
  }
})();
