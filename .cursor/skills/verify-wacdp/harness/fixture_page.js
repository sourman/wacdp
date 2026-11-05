/* Fixture WhatsApp Web. Recorded rows/fibers/archived chats are loaded from
   JSON and installed as DOM + React-fiber props + WAWebChatCollection.
   Clicks, search, compose, and menus answer the same selectors the CDP
   helpers use. This file is verification scaffolding, not WhatsApp. */
(function () {
  const params = new URLSearchParams(location.search);
  const phase = params.get("phase") || "baseline";
  document.documentElement.dataset.phase = phase;

  const state = {
    rows: [],
    fibers: {},
    openId: null,
    quote: null,
    menu: null,
    dialog: null,
    trayMsg: null,
    forward: null,
  };

  const searchEl = document.getElementById("search");
  const pane = document.getElementById("pane-side");
  const main = document.getElementById("main");
  const overlay = document.getElementById("overlay");

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "text") node.textContent = v;
      else if (k === "class") node.className = v;
      else node.setAttribute(k, v);
    }
    for (const child of children || []) node.appendChild(child);
    return node;
  }

  function attachFiber(node, spec) {
    if (!spec) return;
    const chat = { id: { _serialized: spec.id } };
    if (spec.is_group) chat.isGroup = true;
    if (spec.contact) chat.contact = spec.contact;
    // Own enumerable expando, same shape Object.keys walks in scrape_chat_list.py.
    node.__reactFiber$wacdpFixture = { memoizedProps: { chat }, return: null };
  }

  function rowMatches(row, q) {
    if (!q) return !row.archived;
    const query = q.trim();
    if (!query) return !row.archived;
    if (row.who === query) return true;
    const digits = (s) => (s || "").replace(/\D/g, "");
    const qd = digits(query);
    const td = digits(row.who);
    if (qd.length >= 8 && td.length >= 8 && td === qd) return true;
    return row.who.toLowerCase().includes(query.toLowerCase());
  }

  function renderList() {
    pane.textContent = "";
    const q = searchEl.value || "";
    for (const row of state.rows) {
      if (!rowMatches(row, q)) continue;
      const fiber = el("div", { class: "fiber" });
      fiber.appendChild(el("span", { title: row.who, class: "who", dir: "auto", text: row.who }));
      const time = el("div", { class: "time", text: row.time || "" });
      const secondary = el("div", { class: "secondary", "data-testid": "cell-frame-secondary" });
      if (row.sender) {
        secondary.appendChild(el("div", { text: row.sender }));
        secondary.appendChild(el("div", { text: ":" }));
        secondary.appendChild(el("div", { text: row.preview || "" }));
      } else if (row.preview) {
        secondary.appendChild(el("div", { text: row.preview }));
      }
      if (row.time_after) {
        if (row.preview || row.sender) fiber.appendChild(secondary);
        fiber.appendChild(time);
      } else {
        fiber.appendChild(time);
        if (row.preview || row.sender) fiber.appendChild(secondary);
      }
      if (row.unread) {
        fiber.appendChild(el("span", {
          class: "badge",
          "aria-label": row.unread + " unread messages",
          text: String(row.unread),
        }));
      }
      if (row.muted) {
        fiber.appendChild(el("span", { "data-icon": "muted", "aria-label": "Muted chat" }));
      }
      if (row.group) {
        fiber.appendChild(el("span", { "data-icon": "default-group" }));
      }
      if (row.from_me === "tick") {
        const status = el("span", { "data-testid": "last-msg-status", class: "status-icon" });
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = "wds-ic-delivered";
        svg.appendChild(title);
        status.appendChild(svg);
        fiber.appendChild(status);
      }
      attachFiber(fiber, state.fibers[row.id]);
      const item = el("div", { role: "listitem", "data-fixture-id": row.id, class: "row" }, [fiber]);
      item.addEventListener("click", () => openChat(row.id));
      item.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        openRowMenu(row, ev.clientX, ev.clientY);
      });
      pane.appendChild(item);
    }
  }

  function bubbleNode(msg, row) {
    const wrap = el("div", { class: "focusable-list-item msg " + (msg.dir === "out" ? "out" : "in") });
    const bubble = el("div", { "data-testid": "msg-container", class: "bubble" });
    if (msg.dir === "out" && !msg.recalled) {
      bubble.appendChild(el("span", { "aria-label": "You:", text: "You:" }));
    }
    if (msg.forwarded) bubble.appendChild(el("span", { "data-testid": "forwarded", text: "Forwarded" }));
    if (msg.quote) bubble.appendChild(el("div", { "data-testid": "quoted-message", class: "quoted", text: msg.quote }));
    bubble.appendChild(el("span", { "data-testid": "selectable-text", text: msg.text }));
    if (msg.dir === "out" && !msg.recalled) {
      bubble.appendChild(el("span", {
        "data-testid": "msg-dblcheck",
        "data-icon": "msg-dblcheck",
        "aria-label": "Delivered",
      }));
    }
    const opts = el("button", {
      "data-testid": "icon-down-context",
      "aria-label": "Message options",
      type: "button",
      text: "v",
    });
    opts.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openMsgMenu(row, msg, ev.clientX, ev.clientY);
    });
    bubble.appendChild(opts);
    wrap.appendChild(bubble);
    if (msg.dir === "in") {
      const react = el("button", {
        "data-testid": "reaction-entry-point",
        "aria-label": "React",
        type: "button",
        text: "☺",
      });
      react.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openTray(wrap, msg, row);
      });
      wrap.appendChild(react);
    }
    if (msg.reaction) {
      wrap.appendChild(el("div", { "data-testid": "reaction-bubble", "aria-label": "You reacted " + msg.reaction }, [
        el("img", { alt: msg.reaction }),
      ]));
    }
    return wrap;
  }

  function renderMain() {
    main.textContent = "";
    const row = state.rows.find((r) => r.id === state.openId);
    if (!row) return;
    const header = el("header");
    header.appendChild(el("span", { title: row.who, dir: "auto", text: row.who }));
    if (row.group) {
      header.appendChild(el("span", { "data-icon": "default-group", "data-testid": "default-group" }));
      header.appendChild(el("div", { "data-testid": "chat-subtitle", text: "3 participants" }));
    } else {
      header.appendChild(el("div", { "data-testid": "chat-subtitle", text: "click here for contact info" }));
    }
    const menuBtn = el("button", { "aria-label": "Menu", type: "button", text: "Menu" });
    menuBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openHeaderMenu(row, ev.clientX, ev.clientY);
    });
    header.appendChild(menuBtn);
    const fiber = el("div");
    attachFiber(fiber, state.fibers[row.id]);
    header.appendChild(fiber);
    main.appendChild(header);
    const thread = el("div", { id: "thread" });
    for (const msg of row.messages || []) thread.appendChild(bubbleNode(msg, row));
    main.appendChild(thread);
    const footer = el("footer");
    if (state.quote) {
      footer.appendChild(el("div", { "data-testid": "quoted-message", class: "quoted", text: state.quote }));
    }
    const compose = el("div", { contenteditable: "true", "data-tab": "10", role: "textbox" });
    footer.appendChild(compose);
    main.appendChild(footer);
    if (state.focusCompose) {
      compose.focus();
      state.focusCompose = false;
    }
  }

  function renderOverlay() {
    if (state._pickerTimer) {
      clearInterval(state._pickerTimer);
      state._pickerTimer = null;
    }
    const oldTray = document.getElementById("react-tray");
    if (oldTray) oldTray.remove();
    overlay.textContent = "";
    if (state.menu) {
      const menu = el("div", { class: "menu", role: "menu" });
      menu.style.left = state.menu.x + "px";
      menu.style.top = state.menu.y + "px";
      for (const item of state.menu.items) {
        const node = el("div", { role: "menuitem", text: item.label });
        node.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const fn = item.run;
          state.menu = null;
          renderOverlay();
          fn();
        });
        menu.appendChild(node);
      }
      overlay.appendChild(menu);
    }
    if (state.trayMsg) {
      const tray = el("div", { id: "react-tray" });
      const host = state.trayMsg.host;
      const rect = host.getBoundingClientRect();
      tray.style.left = Math.max(440, rect.left) + "px";
      tray.style.top = Math.max(8, rect.top - 48) + "px";
      for (const emoji of ["👍", "❤️", "😂"]) {
        const btn = el("button", { type: "button" });
        btn.appendChild(el("img", { alt: emoji }));
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const msg = state.trayMsg.msg;
          const row = state.rows.find((r) => r.id === state.trayMsg.rowId);
          msg.reaction = emoji;
          if (row) row.preview = "Reacted " + emoji + ' to "' + msg.text + '"';
          state.trayMsg = null;
          renderOverlay();
          renderMain();
          renderList();
        });
        tray.appendChild(btn);
      }
      main.appendChild(tray);
    }
    if (state.forward && state.forward.stage === "bar") {
      const bar = el("div", { id: "forward-bar" });
      const cancel = el("button", { "aria-label": "Cancel forward", type: "button", text: "Cancel" });
      cancel.addEventListener("click", () => {
        state.forward = null;
        renderOverlay();
      });
      const go = el("button", { "aria-label": "Forward", type: "button", text: "Forward" });
      go.addEventListener("click", () => {
        state.forward.stage = "picker";
        state.forward.dest = null;
        renderOverlay();
      });
      bar.appendChild(cancel);
      bar.appendChild(go);
      overlay.appendChild(bar);
    }
    if (state.forward && state.forward.stage === "picker") {
      const dialog = el("div", { class: "dialog", role: "dialog", "data-testid": "confirm-popup" });
      dialog.appendChild(el("div", { text: "Forward message to" }));
      const input = el("input", {
        type: "text",
        "aria-label": "Search name, number or @username",
        placeholder: "Search name, number or @username",
      });
      const list = el("div");
      const paint = () => {
        list.textContent = "";
        const q = input.value || "";
        for (const row of state.rows) {
        if (q && !rowMatches(row, q)) continue;
        if (!q && row.archived) continue;
          const dest = el("div", { class: "dest", role: "listitem" });
          const fiber = el("div", { class: "fiber" });
          fiber.appendChild(el("span", { title: row.who, text: row.who }));
          attachFiber(fiber, state.fibers[row.id]);
          dest.appendChild(fiber);
          dest.addEventListener("click", () => {
            state.forward.dest = row.id;
            dest.setAttribute("data-selected", "1");
          });
          list.appendChild(dest);
        }
      };
      input.addEventListener("input", paint);
      state._pickerTimer = setInterval(() => {
        if ((input.value || "") !== (input.dataset.last || "")) {
          input.dataset.last = input.value || "";
          paint();
        }
      }, 50);
      dialog.appendChild(input);
      dialog.appendChild(list);
      const send = el("button", { "aria-label": "Send", type: "button", text: "Send" });
      send.addEventListener("click", () => {
        const dest = state.rows.find((r) => r.id === state.forward.dest) || state.rows.find((r) => r.id === state.forward.fromId);
        if (!dest) return;
        dest.messages = dest.messages || [];
        dest.messages.push({
          dir: "out",
          text: state.forward.text,
          forwarded: true,
        });
        dest.preview = state.forward.text;
        dest.from_me = "tick";
        dest.time = "1:06 PM";
        const destId = dest.id;
        state.forward = null;
        state.openId = destId;
        renderList();
        renderOverlay();
        renderMain();
      });
      dialog.appendChild(send);
      overlay.appendChild(dialog);
      paint();
    }
    if (state.dialog === "delete-chat") {
      const row = state.rows.find((r) => r.id === state.openId);
      const dialog = el("div", { class: "dialog", role: "dialog", "data-testid": "confirm-popup" });
      dialog.appendChild(el("div", { text: "Delete chat with " + (row ? row.who : "") }));
      const del = el("button", { type: "button", text: "Delete" });
      del.addEventListener("click", () => {
        state.rows = state.rows.filter((r) => r.id !== state.openId);
        state.openId = null;
        state.dialog = null;
        renderList();
        renderOverlay();
        renderMain();
      });
      const cancel = el("button", { type: "button", text: "Cancel" });
      cancel.addEventListener("click", () => {
        state.dialog = null;
        renderOverlay();
      });
      dialog.appendChild(del);
      dialog.appendChild(cancel);
      overlay.appendChild(dialog);
    }
    if (state.dialog === "delete-everyone") {
      const dialog = el("div", { class: "dialog", role: "dialog" });
      const label = el("div", { text: "Delete for everyone" });
      label.addEventListener("click", () => {
        const msg = state.deleteMsg;
        if (msg) {
          msg.text = "You deleted this message";
          msg.recalled = true;
          msg.quote = null;
        }
        state.dialog = null;
        state.deleteMsg = null;
        renderOverlay();
        renderMain();
      });
      dialog.appendChild(label);
      dialog.appendChild(el("div", { text: "Delete for me" }));
      overlay.appendChild(dialog);
    }
  }

  function menuPoint(x, y) {
    const width = 220;
    const height = 180;
    return {
      x: Math.max(8, Math.min(x || 80, window.innerWidth - width)),
      y: Math.max(8, Math.min(y || 80, window.innerHeight - height)),
    };
  }

  function openChat(id) {
    if (state.openId === id && document.querySelector("#main footer [contenteditable='true']")) return;
    state.openId = id;
    state.quote = null;
    state.menu = null;
    renderMain();
    renderOverlay();
  }

  function openRowMenu(row, x, y) {
    const archived = !!row.archived;
    const point = menuPoint(x, y);
    state.menu = {
      x: point.x,
      y: point.y,
      items: [
        {
          label: archived ? "Unarchive chat" : "Archive chat",
          run: () => {
            row.archived = !archived;
            renderList();
          },
        },
      ],
    };
    renderOverlay();
  }

  function openHeaderMenu(row, x, y) {
    const point = menuPoint(x, y);
    state.menu = {
      x: point.x,
      y: point.y,
      items: [
        {
          label: "Delete chat",
          run: () => {
            state.dialog = "delete-chat";
            renderOverlay();
          },
        },
      ],
    };
    renderOverlay();
  }

  function openMsgMenu(row, msg, x, y) {
    const items = [
      {
        label: "Reply",
        run: () => {
          state.openId = row.id;
          state.quote = msg.text;
          state.focusCompose = true;
          renderMain();
        },
      },
      {
        label: "Forward",
        run: () => {
          state.forward = { stage: "bar", text: msg.text, fromId: row.id, dest: null };
          renderOverlay();
        },
      },
    ];
    if (msg.dir === "out") {
      items.push({
        label: "Delete",
        run: () => {
          state.deleteMsg = msg;
          state.dialog = "delete-everyone";
          renderOverlay();
        },
      });
    }
    const point = menuPoint(x, y);
    state.menu = { x: point.x, y: point.y, items };
    renderOverlay();
  }

  function openTray(host, msg, row) {
    state.menu = null;
    state.trayMsg = { host, msg, rowId: row.id };
    renderOverlay();
  }

  function sendCompose() {
    const row = state.rows.find((r) => r.id === state.openId);
    const compose = document.querySelector("#main footer [contenteditable='true']");
    if (!row || !compose) return;
    const text = (compose.innerText || "").trim();
    if (!text) return;
    row.messages = row.messages || [];
    row.messages.push({ dir: "out", text, quote: state.quote || null });
    row.preview = text;
    row.from_me = "tick";
    row.time = "1:07 PM";
    row.unread = 0;
    state.quote = null;
    compose.textContent = "";
    renderList();
    renderMain();
  }

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      const compose = document.querySelector("#main footer [contenteditable='true']");
      if (compose && (document.activeElement === compose || compose.contains(document.activeElement))) {
        ev.preventDefault();
        sendCompose();
      }
    }
    if (ev.key === "Escape") {
      if (state.dialog) {
        state.dialog = null;
        renderOverlay();
        return;
      }
      if (state.trayMsg) {
        state.trayMsg = null;
        renderOverlay();
        return;
      }
      if (state.forward) {
        state.forward = null;
        renderOverlay();
        return;
      }
      if (state.menu) {
        state.menu = null;
        renderOverlay();
        return;
      }
      if (searchEl.value) {
        searchEl.value = "";
        renderList();
        return;
      }
      if (state.openId) {
        state.openId = null;
        state.quote = null;
        renderMain();
      }
    }
  });

  searchEl.addEventListener("input", renderList);
  setInterval(() => {
    if ((searchEl.value || "") !== (searchEl.dataset.last || "")) {
      searchEl.dataset.last = searchEl.value || "";
      renderList();
    }
  }, 50);

  function installArchived(models) {
    const adapted = models.map((m) => {
      const contact = Object.assign({}, m.contact || {});
      return {
        __x_archive: true,
        __x_unreadCount: m.unread || 0,
        id: { _serialized: m.id },
        __x_contact: contact,
        __x_isGroup: !!m.is_group,
        __x_formattedTitle: m.who,
        __x_chatlistPreview: {
          msg: { body: m.preview || "", id: { fromMe: !!m.from_me } },
        },
        __x_muteExpiration: m.muted ? 1 : 0,
      };
    });
    window.require = function (name) {
      if (name === "WAWebChatCollection") {
        return { ChatCollection: { getModelsArray: () => adapted } };
      }
      throw new Error("fixture require missing " + name);
    };
  }

  Promise.all([
    fetch("rows.json").then((r) => r.json()),
    fetch("fibers.json").then((r) => r.json()),
    fetch("archived.json").then((r) => r.json()),
  ]).then(([doc, fibers, archived]) => {
    state.fibers = fibers;
    let rows = doc.rows.map((r) => Object.assign({ messages: [] }, r));
    if (phase === "inbound") {
      rows = rows.map((r) => Object.assign({}, r, (doc.inbound_overrides || {})[r.id] || {}));
    }
    if (phase === "verbs") {
      for (const extra of doc.verbs_extra || []) rows.unshift(Object.assign({ messages: [] }, extra));
      for (const row of rows) {
        if ((doc.messages || {})[row.id]) row.messages = doc.messages[row.id].map((m) => Object.assign({}, m));
      }
    }
    state.rows = rows;
    installArchived(archived);
    renderList();
    renderMain();
    document.documentElement.dataset.ready = "1";
  }).catch((err) => {
    document.documentElement.dataset.ready = "error";
    document.body.dataset.error = String(err);
  });
})();
