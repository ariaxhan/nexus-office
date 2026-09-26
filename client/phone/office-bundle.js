var __defProp = Object.defineProperty;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __esm = (fn, res, err) => function __init() {
  if (err) throw err[0];
  try {
    return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
  } catch (e) {
    throw err = [e], e;
  }
};
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};

// client/phone/office-state.js
function read(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}");
  } catch {
    return {};
  }
}
function persist() {
  localStorage.setItem(CACHE, JSON.stringify(cache));
  localStorage.setItem(PENDING, JSON.stringify(pending));
}
function value(kind, id) {
  return cache[kind]?.[id]?.value;
}
function collection(kind) {
  return Object.values(cache[kind] || {}).filter((item) => item.value !== null).sort((a, b) => b.recorded_at - a.recorded_at).map((item) => item.value);
}
function record(kind, id, value3) {
  const recorded_at = Math.max(Date.now() + clockOffset, (cache[kind]?.[id]?.recorded_at || 0) + 1);
  const item = { kind, id, value: value3, recorded_at };
  (cache[kind] ??= {})[id] = item;
  pending[JSON.stringify([kind, id])] = item;
  persist();
  flush();
}
async function synchronize() {
  try {
    const response = await fetch("/api/user-state", { signal: AbortSignal.timeout(5e3) });
    if (!response.ok) throw Error("User state unavailable");
    const data = await response.json();
    clockOffset = data.server_time - Date.now();
    for (const [kind, items] of Object.entries(data.items)) for (const [id, item] of Object.entries(items)) {
      if (!cache[kind]?.[id] || cache[kind][id].recorded_at < item.recorded_at) (cache[kind] ??= {})[id] = item;
    }
    migrateLegacy();
    persist();
    document.dispatchEvent(new Event("office-user-state"));
    await flush();
  } catch {
  }
}
async function flush() {
  if (running) return;
  running = true;
  try {
    for (const [key, item] of Object.entries(pending)) {
      const response = await fetch("/api/user-state", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item), keepalive: true, signal: AbortSignal.timeout(1e4) });
      if (!response.ok) break;
      const data = await response.json();
      if (pending[key]?.recorded_at === item.recorded_at) {
        delete pending[key];
        (cache[item.kind] ??= {})[item.id] = data.item;
        persist();
      }
    }
  } catch {
  } finally {
    running = false;
  }
}
function discardRemembered() {
  for (const kind of ["recent", "listening", "reading"]) delete cache[kind];
  for (const [key, item] of Object.entries(pending)) if (["recent", "listening", "reading"].includes(item.kind)) delete pending[key];
  persist();
}
function migrateLegacy() {
  const remembering = read("office-preferences-cache").preferences?.remember !== false;
  for (const [kind, key] of [["saved", "office-saved"], ["recent", "office-recent"]]) {
    if (kind === "recent" && !remembering) continue;
    const items = read(key);
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      const id = JSON.stringify([item.kind, item.id]);
      if (!cache[kind]?.[id]) record(kind, id, item);
    }
  }
  if (remembering) {
    for (const [id, position] of Object.entries(read("office-listening"))) if (!cache.listening?.[id]) record("listening", id, position);
  }
}
var CACHE, PENDING, cache, pending, clockOffset, running;
var init_office_state = __esm({
  "client/phone/office-state.js"() {
    CACHE = "office-object-cache";
    PENDING = "office-object-pending";
    cache = read(CACHE);
    pending = read(PENDING);
    clockOffset = 0;
    running = false;
    window.addEventListener("online", synchronize);
    window.addEventListener("pagehide", () => flush());
    setInterval(synchronize, 3e4);
  }
});

// client/phone/office-ui.js
function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== void 0) node.textContent = String(text);
  return node;
}
function button(text, action, className = "") {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", async () => {
    node.disabled = true;
    try {
      await action();
    } catch (error) {
      notice(error.message);
    } finally {
      node.disabled = false;
    }
  });
  return node;
}
async function api(path, body) {
  const response = await fetch(path, { method: body === void 0 ? "GET" : "POST", headers: { Accept: "application/json", ...body === void 0 ? {} : { "Content-Type": "application/json" } }, ...body === void 0 ? {} : { body: JSON.stringify(body) } });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || data.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}
function notice(text) {
  const node = $("#notice");
  node.textContent = text;
  node.hidden = false;
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => node.hidden = true, 6e3);
}
function sheet(title) {
  $("#detail-title").textContent = title;
  const body = $("#detail-body");
  body.transcriptCleanup?.();
  body.onscroll = null;
  body.replaceChildren();
  body.dataset.taskId = "";
  if (!$("#detail").open) $("#detail").showModal();
  return body;
}
function section(parent, title) {
  const part = el("section", "section");
  part.append(el("h2", "section-head", title));
  const body = el("div", "stack");
  part.append(body);
  parent.append(part);
  return body;
}
function card(title, detail2, action) {
  const node = action ? button("", action, "card") : el("article", "card");
  node.append(el("h3", "", title));
  if (detail2) node.append(el("p", "muted", detail2));
  return node;
}
function intro(parent, eyebrow, title, description) {
  const node = el("div", "intro");
  node.append(el("div", "eyebrow", eyebrow), el("h1", "", title));
  if (description) node.append(el("p", "", description));
  parent.append(node);
}
function empty(parent, text) {
  parent.append(el("p", "empty", text));
}
function failure(parent, error) {
  parent.append(el("p", "error", error.message || error));
}
function link(label, url) {
  const node = el("a", "", label);
  let parsed;
  try {
    parsed = new URL(url, location.origin);
  } catch {
    return el("span", "muted", label);
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return el("span", "muted", "Unavailable link");
  node.href = parsed.href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}
function field(parent, label, node) {
  const wrap = el("label", "field");
  node.setAttribute("aria-label", label);
  wrap.append(el("span", "", label), node);
  if (node.tagName === "SELECT") {
    const selected = el("small", "selected-value");
    wrap.append(selected);
    const update = () => {
      const text = node.selectedOptions[0]?.textContent || "";
      selected.textContent = text;
      selected.hidden = text.length < 30;
    };
    node.addEventListener("change", update);
    node.addEventListener("input", update);
    update();
    requestAnimationFrame(update);
  }
  parent.append(wrap);
  return node;
}
function select(options, value3) {
  const node = el("select");
  for (const [id, label] of options) {
    const option = el("option", "", label);
    option.value = id;
    node.append(option);
  }
  node.value = value3;
  return node;
}
function time(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function rememberDetail(kind, id) {
  const value3 = JSON.stringify({ kind, id });
  const url = new URL(location.href);
  if (url.searchParams.get("detail") !== value3) {
    const parent = url.searchParams.get("detail");
    url.searchParams.set("detail", value3);
    history.pushState({ officeDetail: true, officeParent: parent }, "", url);
  }
  if (rememberObjects) {
    record("recent", JSON.stringify([kind, id]), { kind, id, at: Date.now() });
    const prior = JSON.parse(localStorage.getItem("office-recent") || "[]").filter((item) => item.kind !== kind || item.id !== id);
    prior.unshift({ kind, id, at: Date.now() });
    localStorage.setItem("office-recent", JSON.stringify(prior.slice(0, 200)));
  }
}
function saveObject(kind, id, title) {
  record("saved", JSON.stringify([kind, id]), { kind, id, title });
  const prior = JSON.parse(localStorage.getItem("office-saved") || "[]");
  if (!prior.some((item) => item.id === id && item.kind === kind)) prior.unshift({ kind, id, title });
  localStorage.setItem("office-saved", JSON.stringify(prior));
  notice("Saved in Library");
}
function clearDetail() {
  const url = new URL(location.href);
  url.searchParams.delete("detail");
  history.replaceState({}, "", url);
  $("#detail").close();
}
function backDetail() {
  if (history.state?.officeParent) history.back();
  else clearDetail();
}
function transcriptNavigation(body, content) {
  const scroller = body.closest("#detail-body") || body;
  const nav = el("nav", "transcript-nav");
  nav.setAttribute("aria-label", "Conversation navigation");
  const picker = select([], "");
  picker.setAttribute("aria-label", "Jump to message");
  let following = true, nodes = [], index = 0;
  function jump(next) {
    index = Math.max(0, Math.min(nodes.length - 1, next));
    following = false;
    if (nodes[index]) scroller.scrollTop += nodes[index].getBoundingClientRect().top - nav.getBoundingClientRect().bottom - 16;
    picker.value = String(index);
  }
  nav.append(button("First", () => jump(0)), button("Previous", () => jump(index - 1)), button("Next", () => jump(index + 1)), button("Latest", () => {
    following = true;
    index = nodes.length - 1;
    picker.value = String(index);
    scroller.scrollTop = scroller.scrollHeight;
  }), picker);
  picker.addEventListener("change", () => jump(Number(picker.value)));
  body.insertBefore(nav, body.firstChild);
  function update() {
    nodes = [...content.children].filter((node) => node.textContent.trim());
    picker.replaceChildren();
    nodes.forEach((node, i) => {
      const option = el("option", "", `Message ${i + 1}`);
      option.value = String(i);
      picker.append(option);
    });
    picker.value = String(Math.min(index, nodes.length - 1));
    if (following) requestAnimationFrame(() => {
      if (nav.isConnected) {
        index = nodes.length - 1;
        picker.value = String(index);
        scroller.scrollTop = scroller.scrollHeight;
      }
    });
  }
  function scroll() {
    following = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
    const top = nav.getBoundingClientRect().bottom;
    const visible = nodes.findIndex((node) => node.getBoundingClientRect().bottom > top + 8);
    index = following ? nodes.length - 1 : Math.max(0, visible);
    picker.value = String(index);
  }
  scroller.addEventListener("scroll", scroll, { passive: true });
  const observer = new MutationObserver(update);
  observer.observe(content, { childList: true, subtree: true, characterData: true });
  scroller.transcriptCleanup = () => {
    observer.disconnect();
    scroller.removeEventListener("scroll", scroll);
  };
  update();
  return nav;
}
function restoreDraft(input, identity) {
  const key = "office-message-draft:" + JSON.stringify(identity);
  input.value = localStorage.getItem(key) || "";
  input.addEventListener("input", () => localStorage.setItem(key, input.value));
  return (sent) => {
    if (input.value !== sent || localStorage.getItem(key) !== sent) return;
    input.value = "";
    localStorage.removeItem(key);
  };
}
var $, rememberObjects;
var init_office_ui = __esm({
  "client/phone/office-ui.js"() {
    init_office_state();
    $ = (selector) => document.querySelector(selector);
    rememberObjects = true;
    document.addEventListener("office-preferences", (event) => {
      rememberObjects = event.detail.remember;
      if (!rememberObjects) {
        localStorage.removeItem("office-recent");
        discardRemembered();
      }
    });
  }
});

// client/phone/office-native.js
function nativeCommand(data) {
  window.webkit?.messageHandlers?.officeNative?.postMessage(data);
}
function audioTransport(fallback) {
  return window.officeNativeAvailable ? new NativeAudio() : fallback;
}
var NativeAudio;
var init_office_native = __esm({
  "client/phone/office-native.js"() {
    NativeAudio = class extends EventTarget {
      constructor() {
        super();
        this.isNative = true;
        this.state = { position: 0, duration: 0, paused: true, rate: 1 };
        this.metadata = {};
        window.addEventListener("office-native-audio", (event) => this.receive(event.detail));
      }
      configure(episode) {
        this.metadata = { title: episode.title, id: episode.id };
      }
      set src(url) {
        this.state = { ...this.state, position: 0, duration: 0, paused: true };
        nativeCommand({ command: "load", url: new URL(url, location.origin).href, ...this.metadata });
      }
      get currentTime() {
        return this.state.position;
      }
      set currentTime(position) {
        nativeCommand({ command: "seek", position });
      }
      get duration() {
        return this.state.duration;
      }
      get paused() {
        return this.state.paused;
      }
      get playbackRate() {
        return this.state.rate;
      }
      set playbackRate(rate) {
        nativeCommand({ command: "rate", rate });
      }
      play() {
        nativeCommand({ command: "play" });
        return Promise.resolve();
      }
      pause() {
        nativeCommand({ command: "pause" });
      }
      receive(data) {
        const prior = this.state.paused;
        this.state = { ...this.state, ...data };
        if (prior !== this.state.paused) this.dispatchEvent(new Event(this.state.paused ? "pause" : "play"));
        this.dispatchEvent(new Event(data.event));
      }
    };
  }
});

// client/phone/office-settings.js
async function loadSettings() {
  const cached = localStorage.getItem("office-preferences-cache");
  if (cached) {
    try {
      acceptPreferences(JSON.parse(cached));
    } catch {
      localStorage.removeItem("office-preferences-cache");
    }
  }
  acceptPreferences(await api("/api/preferences"));
}
function acceptPreferences(data) {
  prefs = { ...prefs, ...data.preferences };
  revision = data.revision;
  localStorage.setItem("office-preferences-cache", JSON.stringify(data));
  apply();
}
function apply() {
  const body = document.body;
  const theme = prefs.theme === "auto" ? dark.matches ? "dark" : "light" : prefs.theme;
  body.dataset.theme = prefs.background ? luminance(prefs.background) < 0.18 ? "dark" : "light" : theme;
  applyContrast(body);
  body.style.setProperty("--accent-ink", luminance(prefs.accent || "#b9573c") > 0.18 ? "#171915" : "#ffffff");
  body.dataset.motion = String(prefs.motion);
  body.style.setProperty("--accent", prefs.accent || "#b9573c");
  body.style.setProperty("--bg", prefs.background || "");
  body.style.setProperty("--font", { classic: "Georgia,serif", system: "system-ui,sans-serif", literary: "Palatino,Georgia,serif" }[prefs.font]);
  body.style.setProperty("--reading", { bookish: "Georgia,serif", clean: "system-ui,sans-serif", typewriter: "ui-monospace,monospace" }[prefs.reading]);
  body.style.setProperty("--space", { compact: "12px", comfortable: "16px", roomy: "22px" }[prefs.density]);
  body.style.setProperty("--control", prefs.touch ? "52px" : "44px");
  document.documentElement.style.fontSize = `${16 * prefs.size / 100}px`;
  nativeCommand({ command: "preferences", remember: prefs.remember });
  document.dispatchEvent(new CustomEvent("office-preferences", { detail: prefs }));
}
async function save(update) {
  const data = await api("/api/preferences", { revision, preferences: update });
  acceptPreferences(data);
}
function setting(parent, key, title, control, description = "") {
  const row = el("div", "setting");
  const label = el("label", "", title);
  control.id = `pref-${key}`;
  label.htmlFor = control.id;
  if (description) label.append(el("small", "", description));
  row.append(label, control);
  parent.append(row);
  control.addEventListener("change", async () => {
    control.disabled = true;
    try {
      await save({ [key]: value2(control) });
    } catch (error) {
      notice(error.message);
      await loadSettings();
    } finally {
      control.disabled = false;
    }
  });
  return control;
}
function value2(control) {
  if (control.type === "checkbox") return control.checked;
  if (control.type === "range") return Number(control.value);
  return control.value;
}
function choice(parent, key, title, options) {
  return setting(parent, key, title, select(options, prefs[key]));
}
function toggle(parent, key, title, description) {
  const control = el("input");
  control.type = "checkbox";
  control.checked = prefs[key];
  return setting(parent, key, title, control, description);
}
function range(parent, key, title, min, max, step) {
  const node = el("input");
  Object.assign(node, { type: "range", min, max, step, value: prefs[key] });
  return setting(parent, key, title, node, `${prefs[key]}${key === "size" ? "%" : "\xD7"}`);
}
function color(parent, key, title) {
  const node = el("input");
  node.type = "color";
  node.value = prefs[key] || "#f5f1e8";
  return setting(parent, key, title, node);
}
function settings() {
  rememberDetail("settings", "preferences");
  const body = sheet("Settings");
  if (window.officeNativeAvailable) body.append(button("Mac connection", () => nativeCommand({ command: "configure" })));
  const look = section(body, "Appearance");
  choice(look, "theme", "Theme", [["auto", "Follow device"], ["light", "Paper"], ["dark", "Night"]]);
  color(look, "background", "Background color");
  look.append(button("Use theme background", () => save({ background: "" })));
  color(look, "accent", "Accent color");
  const type = section(body, "Typography & spacing");
  choice(type, "font", "Interface font", [["classic", "Classic"], ["system", "System"], ["literary", "Literary"]]);
  choice(type, "reading", "Reading font", [["bookish", "Bookish"], ["clean", "Clean"], ["typewriter", "Typewriter"]]);
  range(type, "size", "Text size", 90, 150, 5);
  choice(type, "density", "Density", [["compact", "Compact"], ["comfortable", "Comfortable"], ["roomy", "Roomy"]]);
  toggle(type, "touch", "Larger controls", "More space for your thumb.");
  const feel = section(body, "Interaction");
  const haptics = toggle(feel, "haptics", "Haptics", "Short taps on supported devices.");
  if (!navigator.vibrate && !window.officeNativeAvailable) {
    haptics.disabled = true;
    haptics.closest(".setting").querySelector("small").textContent = "This browser does not provide haptics. Your preference is saved for supported devices.";
  }
  toggle(feel, "motion", "Reduce motion", "Keep transitions still.");
  const media = section(body, "Reading & listening");
  toggle(media, "remember", "Remember position", "Sync reading and podcast progress through your Mac.");
  toggle(media, "mini", "Keep player visible", "Playback continues when the compact player is hidden.");
  range(media, "speed", "Playback speed", 0.5, 2, 0.05);
  body.append(button("Reset all settings", async () => {
    const data = await api("/api/preferences", { revision, reset: true });
    acceptPreferences(data);
    settings();
    notice("Settings reset");
  }));
}
function luminance(hex) {
  const channels = hex.slice(1).match(/../g).map((value3) => parseInt(value3, 16) / 255).map((value3) => value3 <= 0.04045 ? value3 / 12.92 : ((value3 + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
async function setPlaybackSpeed(speed) {
  return save({ speed });
}
function applyContrast(body) {
  const night = body.dataset.theme === "dark";
  const ink = night ? "#ffffff" : "#000000";
  body.style.setProperty("--ink", prefs.background ? ink : "");
  body.style.setProperty("--muted", prefs.background ? ink : "");
  const background = prefs.background || (night ? "#232722" : "#f5f1e8");
  const paper = night ? "#2c302a" : "#fffcf5";
  const accent = prefs.accent || "#b9573c";
  const contrast = (color2) => {
    const a = luminance(accent), b = luminance(color2);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  body.style.setProperty("--accent-text", Math.min(contrast(background), contrast(paper)) >= 4.5 ? accent : ink);
}
var prefs, revision, dark;
var init_office_settings = __esm({
  "client/phone/office-settings.js"() {
    init_office_native();
    init_office_ui();
    prefs = { theme: "auto", background: "", accent: "#b9573c", font: "classic", reading: "bookish", size: 100, density: "comfortable", touch: false, haptics: true, motion: false, remember: true, mini: true, speed: 1 };
    revision = 0;
    dark = matchMedia("(prefers-color-scheme: dark)");
    dark.addEventListener("change", apply);
    document.addEventListener("click", (event) => {
      if (!prefs.haptics || !event.target.closest("button,a")) return;
      if (window.officeNativeAvailable) nativeCommand({ command: "haptic" });
      else navigator.vibrate?.(8);
    });
  }
});

// client/phone/office-markdown.js
function make(tag, className, value3) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value3 !== void 0) node.textContent = value3;
  return node;
}
function markdownView(raw, options = {}) {
  const view = make("div", "markdown");
  blocks(view, String(raw || "").replace(/\r\n/g, "\n").split("\n"), options.text || plain);
  return view;
}
function blocks(view, lines, text) {
  let paragraph = [];
  const flush2 = () => {
    if (paragraph.length) view.append(inline(make("p"), paragraph.join("\n"), text));
    paragraph = [];
  };
  for (let at = 0; at < lines.length; at++) {
    const line = lines[at];
    if (/^\s*```/.test(line)) {
      flush2();
      const code = [];
      while (++at < lines.length && !/^\s*```/.test(lines[at])) code.push(lines[at]);
      view.append(make("pre", "", code.join("\n")));
      continue;
    }
    if (!line.trim()) {
      flush2();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flush2();
      view.append(inline(make("h" + Math.min(heading[1].length + 1, 6)), heading[2], text));
      continue;
    }
    if (/^\s*>/.test(line)) {
      flush2();
      const quoted = [];
      for (; at < lines.length && /^\s*>/.test(lines[at]); at++) quoted.push(lines[at].replace(/^\s*>\s?/, ""));
      at--;
      const quote = make("blockquote");
      blocks(quote, quoted, text);
      view.append(quote);
      continue;
    }
    if (LIST_ITEM.test(line)) {
      flush2();
      const list = make(/\d/.test(line.match(LIST_ITEM)[1]) ? "ol" : "ul");
      for (; at < lines.length && LIST_ITEM.test(lines[at]); at++) list.append(inline(make("li"), lines[at].match(LIST_ITEM)[2], text));
      at--;
      view.append(list);
      continue;
    }
    if (TABLE_ROW.test(line)) {
      flush2();
      const rows = [];
      for (; at < lines.length && TABLE_ROW.test(lines[at]); at++) rows.push(cells(lines[at]));
      at--;
      view.append(table(rows, text));
      continue;
    }
    paragraph.push(line.trim());
  }
  flush2();
}
function table(rows, text) {
  const node = make("table");
  const divider = rows[1] && rows[1].every((cell) => /^:?-+:?$/.test(cell));
  rows.forEach((row, index) => {
    if (divider && index === 1) return;
    const tr = make("tr");
    for (const cell of row) tr.append(inline(make(divider && index === 0 ? "th" : "td"), cell, text));
    node.append(tr);
  });
  const wrap = make("div", "mdtable");
  wrap.append(node);
  return wrap;
}
function inline(node, raw, text) {
  let from = 0;
  for (const match of raw.matchAll(TOKEN)) {
    if (match.index > from) text(node, raw.slice(from, match.index));
    if (match[1] !== void 0) node.append(anchor(match[1], match[2]));
    else if (match[3] !== void 0) node.append(inline(make("strong"), match[3], text));
    else if (match[4] !== void 0) node.append(make("code", "", match[4]));
    else node.append(inline(make("em"), match[5], text));
    from = match.index + match[0].length;
  }
  if (from < raw.length) text(node, raw.slice(from));
  return node;
}
function anchor(label, href) {
  if (!/^(?:https?:\/\/|#)/i.test(href)) return document.createTextNode(label);
  const link2 = make("a", "", label);
  link2.setAttribute("href", href);
  if (href[0] !== "#") {
    link2.setAttribute("target", "_blank");
    link2.setAttribute("rel", "noopener noreferrer");
  }
  return link2;
}
function tokenText(tokens, render) {
  const byText = new Map(tokens.map((token2) => [token2.text, token2]));
  if (!byText.size) return plain;
  const escaped = [...byText.keys()].sort((a, b) => b.length - a.length).map((key) => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(escaped.join("|"), "g");
  return (node, value3) => {
    let from = 0;
    for (const match of value3.matchAll(pattern)) {
      if (match.index > from) plain(node, value3.slice(from, match.index));
      node.append(render(byText.get(match[0])));
      from = match.index + match[0].length;
    }
    if (from < value3.length) plain(node, value3.slice(from));
  };
}
var plain, TABLE_ROW, LIST_ITEM, cells, TOKEN;
var init_office_markdown = __esm({
  "client/phone/office-markdown.js"() {
    plain = (node, value3) => node.append(document.createTextNode(value3));
    TABLE_ROW = /^\s*\|.*\|\s*$/;
    LIST_ITEM = /^\s*([-*+]|\d+[.)])\s+(.*)$/;
    cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
    TOKEN = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|`([^`]+)`|\*([^*\s][^*]*)\*/g;
  }
});

// client/phone/office-files.js
var office_files_exports = {};
__export(office_files_exports, {
  browse: () => browse,
  numberedSource: () => numberedSource,
  openFile: () => openFile
});
async function browse(parent, id = "", cursor = 0) {
  if (cursor === 0 && parent.closest("dialog")) rememberDetail("folder", id);
  const data = await api(`/api/objects?id=${encodeURIComponent(id)}&cursor=${cursor}`);
  if (cursor === 0) {
    parent.replaceChildren();
    if (id) parent.append(button("\u2191 Parent folder", () => browse(parent, data.parent || "")));
    if (data.path) parent.append(el("p", "muted", data.path));
    if (id) checkoutDetails(parent, id);
  }
  for (const item of data.items) {
    parent.append(card(item.name, item.kind === "folder" ? item.path || "" : `${item.mime} \xB7 ${Math.ceil(item.bytes / 1024)} KB`, () => item.kind === "folder" ? browse(sheet(item.name), item.id) : openFile(item.id)));
  }
  if (!data.items.length) empty(parent, "This folder has no visible work files.");
  if (data.next_cursor !== null) parent.append(button("More files", () => browse(parent, id, data.next_cursor)));
}
function draft(id) {
  try {
    return JSON.parse(localStorage.getItem(`office-draft:${id}`) || "null");
  } catch {
    return null;
  }
}
async function openFile(id, offset = 0) {
  rememberDetail("file", id);
  const data = await api(`/api/objects/detail?id=${encodeURIComponent(id)}&offset=${offset}`);
  const body = sheet(data.name);
  body.append(el("p", "muted", `${data.project} / ${data.path}`), el("p", "muted", `Revision ${data.revision.slice(0, 12)}${data.is_text ? ` \xB7 lines ${data.line_start}\u2013${data.line_end}` : ""}`));
  checkoutDetails(body, id);
  const actions = el("div", "actions");
  actions.append(link("Download", data.content_url), button("Save to Library", () => saveObject("file", id, data.name)), button("Ask an agent", () => document.dispatchEvent(new CustomEvent("office-file-task", { detail: data }))));
  body.append(actions);
  if (data.mime === "text/html") actions.append(button("Preview", () => preview(sheet(data.name), data)));
  if (data.editable) actions.append(button("Edit file", () => editFile(data)));
  if (data.is_text) {
    actions.append(button("View source", () => {
      const source = sheet(data.name + " source");
      source.append(numberedSource(data));
      source.append(contextSelection(data));
    }));
    const text = data.name.endsWith(".md") ? markdownView(data.text) : el("pre", "", data.text);
    text.classList.add("reading");
    body.append(text);
    restoreReading(body, id);
  } else preview(body, data);
  if (data.is_text) body.append(contextSelection(data));
  if (data.next_offset !== null) body.append(button("Next part", () => openFile(id, data.next_offset)));
  if (offset > 0) body.append(button("Start of file", () => openFile(id)));
}
function preview(body, data) {
  if (data.mime.startsWith("image/")) {
    const image = el("img");
    image.src = data.content_url;
    image.alt = data.name;
    image.style.maxWidth = "100%";
    body.append(image);
    return;
  }
  if (data.mime.startsWith("video/") || data.mime.startsWith("audio/")) {
    const media = el(data.mime.startsWith("video/") ? "video" : "audio");
    media.controls = true;
    media.src = data.content_url;
    media.style.width = "100%";
    body.append(media);
    return;
  }
  if (data.mime === "application/pdf" || data.mime === "text/html") {
    const frame = el("iframe", "preview");
    frame.title = data.name;
    frame.src = data.content_url;
    frame.setAttribute("sandbox", "allow-scripts");
    body.append(frame);
    return;
  }
  empty(body, "This format is available as a download.");
}
function restoreReading(body, id) {
  if (!prefs.remember) return;
  body.scrollTop = Number(value("reading", id) ?? localStorage.getItem(`office-read:${id}`) ?? 0);
  let timer;
  body.onscroll = () => {
    if (!prefs.remember) return;
    const position = body.scrollTop;
    localStorage.setItem(`office-read:${id}`, String(position));
    clearTimeout(timer);
    timer = setTimeout(() => record("reading", id, position), 300);
  };
}
function editFile(data) {
  const body = sheet(`Edit ${data.name}`);
  const prior = draft(data.id);
  const editor = el("textarea", "code-editor");
  editor.setAttribute("aria-label", `Edit ${data.name}`);
  editor.value = prior?.text ?? data.text;
  let revision2 = prior?.revision ?? data.revision;
  if (prior && prior.revision !== data.revision) {
    body.append(el("p", "error", "Your saved draft is based on an older file. Compare with the current file before replacing it."));
    const compare = el("details");
    compare.append(el("summary", "", "Current file"), el("pre", "", data.text));
    body.append(compare);
    body.append(button("Use current file as comparison base", () => {
      revision2 = data.revision;
      notice("Base updated. Review your draft before saving.");
    }));
  }
  editor.addEventListener("input", () => localStorage.setItem(`office-draft:${data.id}`, JSON.stringify({ text: editor.value, revision: revision2 })));
  body.append(editor);
  const actions = el("div", "actions");
  const preview2 = el("pre");
  body.append(preview2);
  actions.append(button("Preview changes", async () => {
    const dataDiff = await api("/api/objects/diff", { id: data.id, text: editor.value, revision: revision2 });
    preview2.textContent = dataDiff.diff || "No changes";
  }), button("Save changes", async () => {
    await api("/api/objects/save", { id: data.id, text: editor.value, revision: revision2 });
    localStorage.removeItem(`office-draft:${data.id}`);
    notice("Saved on your Mac");
    await openFile(data.id);
  }, "primary"), button("Discard draft", () => {
    localStorage.removeItem(`office-draft:${data.id}`);
    return openFile(data.id);
  }));
  body.append(actions, el("p", "muted", "Drafts stay on this device until saved. A conflicting Mac edit is never silently overwritten."));
}
function numberedSource(data) {
  const body = el("div");
  const lines = data.text.split("\n");
  let count = 0;
  const list = el("ol", "code-lines");
  list.start = data.line_start || 1;
  body.append(list);
  const more = button("More source lines", append);
  body.append(more);
  function append() {
    for (const line of lines.slice(count, count + 500)) {
      const item = el("li");
      item.append(el("code", "", line || " "));
      list.append(item);
    }
    count += 500;
    more.hidden = count >= lines.length;
  }
  append();
  return body;
}
function contextSelection(data) {
  const box = el("details");
  box.append(el("summary", "", "Ask about selected lines"));
  const start = el("input"), end = el("input");
  for (const [input, label, value3] of [[start, "First line", data.line_start], [end, "Last line", data.line_end]]) {
    input.type = "number";
    input.min = 1;
    input.value = value3;
    input.setAttribute("aria-label", label);
    const field2 = el("label");
    field2.append(el("span", "", label), input);
    box.append(field2);
  }
  box.append(button("Ask an agent about these lines", () => document.dispatchEvent(new CustomEvent("office-file-task", { detail: { ...data, start_line: Number(start.value), end_line: Number(end.value) } }))));
  return box;
}
async function checkoutDetails(parent, id) {
  const details = el("details", "card");
  details.append(el("summary", "", "File location & checkout"));
  const info = el("p", "muted", "Reading checkout status\u2026");
  details.append(info);
  parent.append(details);
  try {
    const data = await api("/api/objects/provenance?id=" + encodeURIComponent(id));
    if (!details.isConnected) return;
    info.textContent = data.state === "checkout" ? `${data.source} \xB7 ${data.branch} \xB7 ${data.dirty ? "Uncommitted changes" : "Clean checkout"} \xB7 commit ${data.commit}` : `${data.source} \xB7 ${data.path}`;
  } catch (error) {
    info.textContent = "Checkout status unavailable: " + error.message;
  }
}
var init_office_files = __esm({
  "client/phone/office-files.js"() {
    init_office_state();
    init_office_ui();
    init_office_markdown();
    init_office_settings();
    document.addEventListener("office-preferences", () => {
      if (!prefs.remember) {
        for (const key of Object.keys(localStorage)) if (key.startsWith("office-read:")) localStorage.removeItem(key);
      }
    });
  }
});

// client/phone/office.js
init_office_state();

// client/phone/office-attachments.js
init_office_ui();
function attachments(parent, initial, onChange, options = {}) {
  let values = [...initial || []];
  const panel = el("div", "stack");
  parent.append(panel);
  const input = el("input");
  input.type = "file";
  input.multiple = true;
  input.setAttribute("aria-label", "Attach photos or files");
  if (options.imagesOnly) input.accept = "image/png,image/jpeg,image/gif,image/webp";
  const progress = el("p", "muted");
  progress.setAttribute("role", "status");
  panel.append(progress);
  const list = el("div", options.compact ? "ask-image-list" : "stack");
  if (options.compact) {
    input.hidden = true;
    panel.append(input, button("\uFF0B Image", () => input.click(), "ask-attach-button"));
  } else panel.append(el("p", "muted", "Photos or files \xB7 up to eight, 5 MiB each. Uploaded to your Mac."), input);
  panel.append(list);
  function draw() {
    list.replaceChildren();
    for (const item of values) {
      const row = el("div", options.compact ? "ask-image-chip" : "row");
      const url = `/api/uploads/content?id=${encodeURIComponent(item.id)}&revision=${item.revision}`;
      if (options.imagesOnly) {
        const preview2 = el("img");
        preview2.src = url;
        preview2.alt = "";
        row.append(preview2);
      }
      row.append(link(item.name || "Attached file", url), button("Remove", () => {
        values = values.filter((other) => other.id !== item.id);
        onChange(values);
        draw();
      }));
      list.append(row);
    }
  }
  input.addEventListener("change", async () => {
    input.disabled = true;
    progress.textContent = "Uploading to your Mac\u2026";
    try {
      for (const file of input.files) {
        if (values.length >= 8) throw Error("Attach at most eight files.");
        if (file.size > 5 * 1024 * 1024) throw Error(`${file.name} exceeds 5 MiB.`);
        if (options.imagesOnly && !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)) throw Error("Choose a PNG, JPEG, GIF, or WebP image.");
        const encoded = await encode(file);
        const receipt = await api("/api/uploads", { name: file.name, base64: encoded });
        if (!values.some((item) => item.id === receipt.id)) values.push(receipt);
        onChange(values);
        draw();
      }
    } catch (error) {
      notice(error.message);
    } finally {
      input.disabled = false;
      input.value = "";
      progress.textContent = "";
    }
  });
  draw();
  return { ready: () => !input.disabled, references: () => values.map(({ id, revision: revision2 }) => ({ id, revision: revision2 })), clear: () => {
    values = [];
    onChange(values);
    draw();
  } };
}
function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(Error("Could not read the selected file"));
    reader.readAsDataURL(file);
  });
}

// client/phone/office.js
init_office_ui();
init_office_settings();

// client/phone/office-media.js
init_office_files();
init_office_native();
init_office_state();
init_office_ui();
init_office_settings();
var audio = audioTransport($("#audio"));
var current = null;
var lastSave = 0;
function positions() {
  try {
    return JSON.parse(localStorage.getItem("office-listening") || "{}");
  } catch {
    return {};
  }
}
function remember() {
  if (!current || !prefs.remember) return;
  const data = positions();
  data[current.id] = audio.currentTime;
  record("listening", current.id, audio.currentTime);
  localStorage.setItem("office-listening", JSON.stringify(data));
}
function seek(delta) {
  audio.currentTime = Math.min(audio.duration || Infinity, Math.max(0, audio.currentTime + delta));
}
async function play(episode) {
  if (current?.id !== episode.id) {
    remember();
    current = episode;
    audio.configure?.(episode);
    audio.src = episode.url;
    audio.playbackRate = prefs.speed;
    audio.addEventListener("loadedmetadata", () => {
      audio.currentTime = prefs.remember ? value("listening", episode.id) ?? positions()[episode.id] ?? 0 : 0;
    }, { once: true });
  }
  renderPlayer();
  await audio.play();
  mediaSession();
}
function mediaSession() {
  if (audio.isNative || !("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({ title: current.title, artist: "Nexus Office", album: "Your private briefing" });
  const handlers = { play: () => audio.play(), pause: () => audio.pause(), seekbackward: () => seek(-15), seekforward: () => seek(15), seekto: (event) => audio.currentTime = event.seekTime };
  for (const [name, handler] of Object.entries(handlers)) try {
    navigator.mediaSession.setActionHandler(name, handler);
  } catch {
  }
}
function renderPlayer() {
  const player = $("#player");
  player.hidden = !current || !prefs.mini;
  if (!current) return;
  player.replaceChildren();
  const row = el("div", "row");
  const heading = el("div");
  heading.append(button(current.title, () => mediaDetail(current.id), "title"), el("p", "muted", "Your private podcast"));
  const controls = el("div", "transport");
  controls.append(button("\u221215", () => seek(-15)), button(audio.paused ? "Play" : "Pause", () => audio.paused ? audio.play() : audio.pause(), "play-toggle"), button("+15", () => seek(15)));
  row.append(heading, controls);
  player.append(row);
  const slider = el("input");
  slider.type = "range";
  slider.min = 0;
  slider.max = audio.duration || 1;
  slider.value = audio.currentTime;
  slider.setAttribute("aria-label", "Podcast position");
  slider.id = "mini-seek";
  slider.addEventListener("input", () => audio.currentTime = Number(slider.value));
  player.append(slider);
}
for (const event of ["play", "pause"]) audio.addEventListener(event, () => {
  for (const node of document.querySelectorAll(".play-toggle")) node.textContent = node.dataset.episodeId && node.dataset.episodeId !== current?.id ? "Play episode" : audio.paused ? "Play" : "Pause";
  if (!audio.isNative && "mediaSession" in navigator) navigator.mediaSession.playbackState = audio.paused ? "paused" : "playing";
});
audio.addEventListener("timeupdate", () => {
  const slider = $("#mini-seek");
  if (slider) {
    slider.max = audio.duration || 1;
    slider.value = audio.currentTime;
  }
  const stamp = $("#podcast-time");
  if (stamp && stamp.dataset.episodeId === current?.id) stamp.textContent = `${time(audio.currentTime)} / ${time(audio.duration)}`;
  if (Date.now() - lastSave > 3e3) {
    remember();
    updatePositionState();
    lastSave = Date.now();
  }
});
audio.addEventListener("error", () => notice("Audio could not load. Check your connection to the Mac and retry."));
window.addEventListener("pagehide", remember);
document.addEventListener("office-preferences", () => {
  audio.playbackRate = prefs.speed;
  renderPlayer();
  if (!prefs.remember) localStorage.removeItem("office-listening");
});
async function mediaList(parent, kind = "all", cursor = 0) {
  const data = await api(`/api/media?kind=${encodeURIComponent(kind)}&cursor=${cursor}`);
  for (const error of data.errors) parent.append(el("p", "error", `${error.source}: ${error.error}`));
  for (const item of data.items) {
    const node = card(item.title || item.file, `${item.kind === "podcast" ? Math.round(item.duration_s / 60) + " min \xB7 " : ""}${item.date || ""}`, () => mediaDetail(item.id));
    if (item.excerpt) node.append(el("p", "muted", item.excerpt));
    parent.append(node);
  }
  if (!data.total) empty(parent, "Nothing published here yet.");
  if (data.next_cursor !== null) parent.append(button("Load more", async () => mediaList(parent, kind, data.next_cursor)));
}
async function mediaDetail(id) {
  rememberDetail("media", id);
  const data = await api(`/api/media/detail?id=${encodeURIComponent(id)}`);
  const body = sheet(data.title || "Substrate");
  body.append(button("Save to Library", () => saveObject("media", id, data.title || "Substrate")));
  if (data.kind === "substrate") {
    showPoem(body, data);
    return;
  }
  const controls = el("div", "actions episode-transport");
  controls.append(button("Back 15s", () => seekEpisode(data, -15)), button(current?.id === id && !audio.paused ? "Pause" : "Play episode", () => current?.id === id && !audio.paused ? audio.pause() : play(data), "play-toggle"), button("Ahead 15s", () => seekEpisode(data, 15)));
  controls.querySelector(".play-toggle").dataset.episodeId = id;
  const speed = select([["0.75", "0.75\xD7"], ["1", "1\xD7"], ["1.15", "1.15\xD7"], ["1.25", "1.25\xD7"], ["1.5", "1.5\xD7"], ["2", "2\xD7"]], String(prefs.speed));
  speed.setAttribute("aria-label", "Playback speed");
  speed.addEventListener("change", () => setPlaybackSpeed(Number(speed.value)).catch((error) => notice(error.message)));
  controls.append(speed);
  body.append(controls);
  const stamp = el("p", "muted", `${time(current?.id === id ? audio.currentTime : 0)} / ${time(data.duration_s)}`);
  stamp.id = "podcast-time";
  stamp.dataset.episodeId = id;
  body.append(stamp);
  const chapters = section(body, "Chapters");
  for (const chapter of data.chapters || []) chapters.append(button(`${time(chapter.start_s)} \xB7 ${chapter.title}`, async () => {
    await play(data);
    audio.currentTime = chapter.start_s;
  }));
  if (!data.chapters?.length) empty(chapters, "This older episode has no chapter markers.");
  const transcript = section(body, "Transcript");
  transcript.append(el("div", "reading", data.text || "Transcript unavailable."));
  const sources = section(body, "Sources");
  for (const source of data.sources || []) sources.append(link(source.title, source.url));
}
async function seekEpisode(episode, delta) {
  if (current?.id !== episode.id) await play(episode);
  seek(delta);
}
function updatePositionState() {
  if (audio.isNative || !navigator.mediaSession?.setPositionState || !Number.isFinite(audio.duration) || audio.duration <= 0) return;
  navigator.mediaSession.setPositionState({ duration: audio.duration, playbackRate: audio.playbackRate, position: Math.min(audio.duration, Math.max(0, audio.currentTime)) });
}
function showPoem(body, data) {
  body.append(el("p", "muted", data.date || ""));
  const frame = el("iframe", "preview");
  frame.src = data.url;
  frame.title = data.title || "Computational poem";
  frame.setAttribute("sandbox", "allow-scripts");
  body.append(frame, el("div", "reading", data.text));
  if (data.provenance) poemProvenance(body, data.provenance);
}
function poemProvenance(body, source) {
  const details = el("details");
  details.append(el("summary", "", "Source & publishing"));
  details.append(el("p", "muted", source.publication), el("p", "muted", source.generation_receipt));
  for (const [key, label] of [["source", "Read source"], ["manifest", "Read catalog"], ["pipeline", "How this is generated"]]) if (source[key]) details.append(button(label, () => openFile(source[key])));
  details.append(el("p", "muted", "File SHA256: " + source.sha256));
  if (source.source_commit) details.append(el("p", "muted", `Latest recorded source change: ${source.source_commit.subject} \xB7 ${source.source_commit.at} \xB7 ${source.source_commit.sha}`));
  for (const job of source.configured_producers || []) {
    const url = new URL(location.href);
    url.searchParams.set("detail", JSON.stringify({ kind: "job-history", id: job }));
    details.append(link("Configured producer history: " + job, url.pathname + url.search + url.hash));
  }
  body.append(details);
}

// client/phone/office.js
init_office_files();
init_office_markdown();

// client/phone/office-coordinator.js
init_office_ui();
init_office_files();
init_office_markdown();
var PICK = "office-coordinator-pick";
var pick = (() => {
  try {
    return localStorage.getItem(PICK) || "";
  } catch {
    return "";
  }
})();
var pendingKey = () => "office-coordinator-pending" + (pick && pick !== "tbs" ? "-" + pick : "");
var q = () => pick ? `id=${encodeURIComponent(pick)}` : "";
var clock = (at) => at ? new Date(at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
function token(part) {
  if (part.kind === "url") return link(part.text, part.href);
  const open = {
    issue: () => document.dispatchEvent(new CustomEvent("office-github-detail", { detail: { repo: part.repo, number: part.number } })),
    file: () => openFile(part.id),
    sha: () => commit(part.sha, part.checkout)
  }[part.kind];
  const node = el("button", "coord-link", part.text);
  node.type = "button";
  node.dataset.kind = part.kind;
  node.title = { issue: `${part.repo} #${part.number}`, file: part.path, sha: `Commit in ${part.checkout}` }[part.kind];
  node.addEventListener("click", () => Promise.resolve(open()).catch((error) => notice(error.message)));
  return node;
}
function segments(parent, list) {
  for (const part of list || []) parent.append(part.kind ? token(part) : document.createTextNode(part.text));
  return parent;
}
function rich(parent, list) {
  const text = (list || []).map((part) => part.text).join("");
  parent.append(markdownView(text, { text: tokenText((list || []).filter((part) => part.kind), token) }));
  return parent;
}
async function commit(sha, checkout) {
  const body = sheet(`Commit ${sha.slice(0, 10)}`);
  try {
    const data = await api(`/api/coordinator/commit?sha=${encodeURIComponent(sha)}&checkout=${encodeURIComponent(checkout)}&${q()}`);
    body.append(el("p", "muted", `${data.repo || data.checkout} \xB7 ${data.sha}`));
    if (data.repo) body.append(link("Open on GitHub", `https://github.com/${data.repo}/commit/${data.sha}`));
    const files = el("div", "stack");
    body.append(files);
    for (const file of data.files) {
      const row = el("button", "coord-link coord-file", file.path);
      row.type = "button";
      row.disabled = !file.id;
      if (file.id) row.addEventListener("click", () => openFile(file.id).catch((error) => notice(error.message)));
      files.append(row);
    }
    body.append(el("pre", "", data.text));
  } catch (error) {
    failure(body, error);
  }
}
function runHead(item) {
  const head = el("div", "coord-run");
  const mode = item.mode === "plan" ? "plan run" : "run";
  head.append(el("span", "", `${item.live ? "Live " : ""}${mode} \xB7 ${clock(item.at)}`));
  if (item.live) head.classList.add("live");
  return head;
}
function runEnd(item) {
  const end = item.end;
  if (!end) return item.live ? el("p", "coord-status", "Working. New output appears here as it is written.") : el("p", "coord-status", "No end recorded: the run stopped without finishing its ledger row.");
  const minutes = Math.round((end.secs || 0) / 60);
  return el("p", "coord-status", `Ended ${clock(end.at)} \xB7 ${minutes} min \xB7 exit ${end.rc}${end.timed_out ? " (timed out)" : ""} \xB7 ${end.prod_changes || 0} production changes \xB7 ${end.holds || 0} holds`);
}
function conversation(parent, data) {
  for (const item of data.items) {
    if (item.kind === "message") {
      const node = el("article", "coord-you");
      rich(node.appendChild(el("div", "coord-text")), item.segments);
      node.append(el("small", "", `You \xB7 ${clock(item.at)} \xB7 ${item.acted_at ? `acted ${clock(item.acted_at)}: ${item.action}` : item.read_at ? "read by the coordinator " + clock(item.read_at) : "queued for the coordinator"}`));
      parent.append(node);
      continue;
    }
    parent.append(runHead(item));
    if (item.truncated) parent.append(el("p", "coord-status", "Earlier output from this run is in " + item.log));
    if (!item.events.length) parent.append(el("p", "coord-status", item.live ? "Starting\u2026" : "This run wrote no output."));
    for (const event of item.events) {
      if (event.kind === "tool") {
        const row = el("p", "coord-tool");
        row.append(document.createTextNode(event.text), el("small", "coord-time", clock(event.at)));
        parent.append(row);
        continue;
      }
      const node = el("article", event.kind === "result" ? "coord-say coord-result" : "coord-say");
      rich(node, event.segments);
      node.append(el("small", "coord-time", clock(event.at)));
      parent.append(node);
    }
    for (const hold of item.holds) {
      const node = el("article", "coord-say coord-hold");
      node.append(el("strong", "", "Hold \xB7 "));
      rich(node, hold.segments);
      parent.append(node);
    }
    parent.append(runEnd(item));
  }
  if (!data.items.length) parent.append(el("p", "empty", "No coordinator runs or messages yet."));
}
function changes(parent, data) {
  let any = false;
  for (const item of [...data.items].reverse()) {
    if (item.kind !== "run") continue;
    const found = item.changes;
    if (!found.commits.length && !found.publishes.length && !found.issues.length) continue;
    any = true;
    const group = el("section", "section");
    group.append(el("h2", "section-head", `Run \xB7 ${clock(item.at)}`));
    parent.append(group);
    for (const row of found.publishes) {
      const node = el("p", "coord-change");
      node.append(el("span", "coord-tag", row.outcome === "PASS" ? "Live" : "Publish " + (row.outcome || "")));
      segments(node, [row.id ? { kind: "file", id: row.id, path: row.path, text: row.path } : { text: row.path }]);
      group.append(node);
    }
    for (const row of found.commits) {
      const node = el("p", "coord-change");
      node.append(el("span", "coord-tag", row.checkout));
      segments(node, [{ kind: "sha", sha: row.sha, checkout: row.checkout, text: row.sha.slice(0, 8) }, { text: " " }, ...row.segments || [{ text: row.subject }]]);
      group.append(node);
    }
    for (const row of found.issues) {
      const node = el("p", "coord-change");
      node.append(el("span", "coord-tag", "Issue " + row.action));
      segments(node, row.number ? [{ kind: "issue", repo: row.repo, number: row.number, text: `${row.repo}#${row.number}` }] : [{ text: row.repo }]);
      group.append(node);
    }
  }
  if (!any) parent.append(el("p", "empty", "No landed commits, publishes or issue changes in the recorded runs."));
}
var SYSTEM_NAME = { tbs: "Thinking Brain School", matra: "Matra", office: "Office" };
var SYSTEM_OUTCOME = { tbs: "Keeping lessons healthy and ready for families.", matra: "Fixing app issues and delivering tested improvements.", office: "Moving Office issues, releases, failures and stabilization through Tower." };
var needsAttention = (row) => row.thrashing || ["failing", "stalled", "error"].includes(row.health);
var systemName = (row) => SYSTEM_NAME[row.id] || row.name;
var systemOutcome = (row) => SYSTEM_OUTCOME[row.id] || "Moving its assigned work forward.";
function healthLine(row) {
  const node = el("span", "coord-health");
  node.dataset.health = needsAttention(row) ? "error" : "ok";
  node.textContent = needsAttention(row) ? "Needs attention" : row.health === "idle" ? "Idle" : row.health === "running" ? "Working" : "Working normally";
  return node;
}
function detail(parent, row) {
  parent.replaceChildren();
  if (!row) return;
  if (row.error) {
    parent.append(el("p", "error", row.error));
    return;
  }
  parent.append(coordinatorSummary(row));
  const evidence = el("details", "coord-evidence");
  evidence.append(el("summary", "", "Coordinator notes and work"));
  parent.append(evidence);
  const doing = section(evidence, "Latest coordinator notes");
  doing.append(row.working_on ? markdownView(row.working_on) : el("p", "empty", "No output from the latest run yet."));
  const lanes = section(evidence, "Open lanes");
  for (const lane of row.lanes || []) lanes.append(el("p", "coord-tool", lane));
  if (!(row.lanes || []).length) lanes.append(el("p", "empty", "No lanes named in the latest run."));
  const shipped = section(evidence, "Recent changes");
  for (const c of row.commits || []) {
    const node = el("p", "coord-change");
    node.append(el("span", "coord-tag", c.checkout));
    segments(node, [{ kind: "sha", sha: c.sha, checkout: c.checkout, text: c.sha.slice(0, 8) }, { text: ` ${c.subject} \xB7 ${clock(c.at)}` }]);
    shipped.append(node);
  }
  if (!(row.commits || []).length) shipped.append(el("p", "empty", "No recent changes recorded."));
}
function coordinatorSummary(row) {
  const summary = el("div", "coord-outcome");
  const note = row.id === "office" ? `Last run ${row.age_s == null ? "never" : Math.max(0, Math.floor(row.age_s / 60)) + " min ago"} \xB7 ${row.failures || 0} recent failures \xB7 ${row.changes || 0} recent changes \xB7 ${row.unread || 0} queued messages` : needsAttention(row) ? "I\u2019m checking what needs attention." : "Nothing needed from you.";
  summary.append(el("h1", "coord-system-name", systemName(row)), el("p", "coord-outcome-title", systemOutcome(row)), healthLine(row), el("p", "muted", note));
  if (row.id === "office") {
    const inspect = el("a", "", "Inspect Office");
    inspect.href = "#system";
    summary.append(inspect);
  }
  return summary;
}
async function coordinator(parent) {
  try {
    pick = localStorage.getItem(PICK) || pick;
  } catch {
  }
  const facts = el("div", "coord-facts");
  const activity = el("details", "coord-activity");
  activity.append(el("summary", "", "See activity"));
  const head = el("div", "coord-head");
  const status = el("p", "muted", "Checking status\u2026");
  const tabs = el("div", "coord-switch");
  let view = "chat";
  let rows = [];
  const name = el("div", "eyebrow", "System activity");
  async function drawMaster() {
    const data2 = await api("/api/coordinators");
    rows = data2.coordinators || [];
    const row = rows.find((r) => r.id === (pick || rows[0]?.id)) || rows[0];
    if (row && !pick) {
      pick = row.id;
      try {
        localStorage.setItem(PICK, pick);
      } catch {
      }
    }
    name.textContent = row ? systemName(row) : "System activity";
    status.textContent = row ? needsAttention(row) ? "Needs attention" : "Working normally" : "Status unavailable";
    const evidenceOpen = !!facts.querySelector(".coord-evidence")?.open;
    detail(facts, row);
    if (evidenceOpen) {
      const evidence = facts.querySelector(".coord-evidence");
      if (evidence) evidence.open = true;
    }
  }
  await drawMaster().catch((error) => failure(facts, error));
  parent.append(facts);
  head.append(name, status, tabs);
  activity.append(head);
  const stream = el("div", "coord-stream");
  stream.setAttribute("aria-live", "polite");
  parent.append(stream);
  activity.append(stream);
  parent.append(activity);
  const message = el("details", "coord-message");
  message.append(el("summary", "", "Message this system"));
  const form = el("form", "coord-compose");
  const input = el("textarea");
  input.rows = 2;
  input.placeholder = `Message ${systemName(rows.find((r) => r.id === (pick || rows[0]?.id)) || { id: pick, name: "the system" })}`;
  input.setAttribute("aria-label", "Message the coordinator");
  const send = el("button", "primary", "Send");
  send.type = "submit";
  form.append(input, send);
  message.append(form);
  parent.insertBefore(message, activity);
  const clearDraft = restoreDraft(input, pick && pick !== "tbs" ? ["coordinator", pick] : ["coordinator"]);
  let signature = "", data = null;
  for (const [key, label] of [["chat", "Conversation"], ["changes", "Changes"]]) {
    const b = button(label, () => {
      view = key;
      signature = "";
      stream.dataset.drawn = key === "chat" ? "" : "1";
      draw();
      if (key !== "chat") scrollTo(0, 0);
    });
    b.dataset.view = key;
    tabs.append(b);
  }
  function draw() {
    for (const b of tabs.children) b.setAttribute("aria-pressed", String(b.dataset.view === view));
    if (!data) return;
    const next = view + JSON.stringify(data.items);
    if (next === signature) return;
    signature = next;
    const follow = view === "chat" && (stream.dataset.drawn !== "1" || innerHeight + scrollY >= document.body.scrollHeight - 160);
    stream.replaceChildren();
    (view === "chat" ? conversation : changes)(stream, data);
    stream.dataset.drawn = "1";
    if (follow) requestAnimationFrame(() => scrollTo(0, document.body.scrollHeight));
  }
  let busy = false, idle = false, seen = "";
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      data = await api("/api/coordinator?" + q());
      idle = !data.items.some((item) => item.live);
      const shown = JSON.stringify({ ...data, as_of: "" });
      const live = data.items.some((item) => item.live);
      if (shown !== seen) {
        seen = shown;
        draw();
      }
    } catch (error) {
      status.textContent = "Mac unreachable; showing the last view. " + error.message;
    } finally {
      busy = false;
    }
  }
  async function deliver() {
    const pending2 = JSON.parse(localStorage.getItem(pendingKey()) || "null");
    if (!pending2) return;
    try {
      await api("/api/coordinator/say", pending2);
    } catch (error) {
      if (error.status >= 400 && error.status < 500) localStorage.removeItem(pendingKey());
      throw error;
    }
    localStorage.removeItem(pendingKey());
    clearDraft(pending2.text);
    stream.dataset.drawn = "";
    await refresh();
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!input.value.trim()) return;
    if (!localStorage.getItem(pendingKey())) localStorage.setItem(pendingKey(), JSON.stringify({ id: crypto.randomUUID(), text: input.value, coordinator: pick || void 0 }));
    send.disabled = true;
    try {
      await deliver();
    } catch (error) {
      notice("Not confirmed yet; Send retries the same message. " + error.message);
    } finally {
      send.disabled = false;
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) form.requestSubmit();
  });
  await deliver().catch(() => {
  });
  await refresh();
  let ticks = 0, masterTicks = 0;
  const timer = setInterval(() => {
    if (!parent.isConnected) clearInterval(timer);
    else {
      if (!idle || ++ticks % 5 === 0) refresh();
      if (++masterTicks % 5 === 0) drawMaster().catch(() => {
      });
    }
  }, 3e3);
}

// client/phone/office-tasks.js
init_office_ui();
init_office_markdown();
async function taskList(parent, onlyActive = false, cursor = 0) {
  const data = await api(`/api/tasks?cursor=${cursor}${onlyActive ? "&active=1" : ""}`);
  for (const item of data.items) {
    if (onlyActive && item.flight?.state !== "running" && item.flight?.state !== "queued") continue;
    parent.append(card(item.title, `${item.specification.engine} \xB7 ${item.specification.profile} \xB7 ${item.phase}`, () => taskDetail(item.id)));
  }
  if (data.next_cursor !== null) parent.append(button("Older conversations", () => taskList(parent, onlyActive, data.next_cursor)));
  return data.items;
}
async function newTask(project2 = "", context = null) {
  const body = sheet("Start something");
  body.append(el("p", "muted", "Runs on your Mac in a disposable clone."));
  const loading = el("p", "muted", "Checking projects and accounts on your Mac\u2026");
  body.append(loading);
  const data = await api("/api/tasks/capabilities");
  loading.remove();
  body.append(el("p", "muted", data.workspace));
  const controls = composerControls(body, data, project2);
  const draft2 = restoreComposer(controls, context, project2);
  const attached = attachments(body, draft2.uploads, (items) => {
    draft2.uploads = items;
    localStorage.setItem("office-new-task", JSON.stringify(composerPayload(controls, draft2)));
  });
  const status = el("p", "muted");
  body.append(status);
  const start = button("Start task", () => {
    if (!attached.ready()) throw Error("Wait for the attachment upload to finish.");
    return startTask(controls, draft2);
  }, "primary");
  const readiness = () => {
    const ready = data.profiles.find((p) => p.engine === controls.engine.value && p.id === controls.profile.value);
    const pending2 = Boolean(localStorage.getItem("office-new-task-pending"));
    start.disabled = !pending2 && (!ready?.ready || controls.source_ref.disabled);
    start.textContent = pending2 ? "Recover submitted task" : "Start task";
    status.textContent = pending2 ? "Retrying retrieves the exact submitted task; edits stay in your next draft." : controls.source_ref.disabled ? controls.source_ref.dataset.error || "Loading saved branches from your Mac\u2026" : ready?.detail || "This account is unavailable.";
  };
  for (const control of Object.values(controls)) control.addEventListener("input", () => {
    localStorage.setItem("office-new-task", JSON.stringify(composerPayload(controls, draft2)));
    readiness();
  });
  body.append(start);
  readiness();
  const refreshSource = () => {
    controls.source_ref.disabled = true;
    readiness();
    return loadSources(controls, body).finally(() => {
      readiness();
      retry.hidden = !controls.source_ref.dataset.error;
    });
  };
  const retry = button("Retry loading branches", refreshSource);
  retry.hidden = true;
  body.append(retry);
  controls.project.addEventListener("change", refreshSource);
  await refreshSource();
}
async function startTask(controls, draft2) {
  const key = "office-new-task-pending";
  const current2 = composerPayload(controls, draft2);
  let pending2 = JSON.parse(localStorage.getItem(key) || "null");
  if (!pending2) {
    if (!current2.prompt.trim()) throw Error("Describe the task first.");
    pending2 = current2;
    localStorage.setItem(key, JSON.stringify(pending2));
  }
  let result;
  try {
    result = await api("/api/tasks/start", pending2);
  } catch (error) {
    if ([400, 403, 404, 409, 413, 422].includes(error.status)) localStorage.removeItem(key);
    throw error;
  }
  localStorage.removeItem(key);
  if (JSON.stringify(current2) === JSON.stringify(pending2)) localStorage.removeItem("office-new-task");
  else {
    draft2.request_id = crypto.randomUUID();
    localStorage.setItem("office-new-task", JSON.stringify(composerPayload(controls, draft2)));
  }
  notice("Accepted by your Mac");
  await taskDetail(result.task_id);
}
function composerControls(body, data, project2) {
  const selected = data.projects.find((p) => p.id === project2 || p.name === project2) || data.projects[0];
  return {
    prompt: field(body, "What would you like to do?", el("textarea")),
    project: field(body, "Project", select(data.projects.map((p) => [p.id, p.label || p.name]), selected?.id)),
    source_ref: field(body, "Starting branch", select([["HEAD", "Current checkout"]], "HEAD")),
    engine: field(body, "Agent", select([["codex", "Codex"], ["claude", "Claude Code"]], "codex")),
    profile: field(body, "Account", select([["personal", "Personal"], ["tbs", "TBS"]], "personal"))
  };
}
function restoreComposer(controls, context, project2) {
  const saved = JSON.parse(localStorage.getItem("office-new-task") || "{}");
  for (const [key, control] of Object.entries(controls)) if (saved[key] && !(key === "project" && project2)) control.value = saved[key];
  controls.source_ref.dataset.savedValue = saved.source_ref || "HEAD";
  const reference = context ? { id: context.id, revision: context.revision, ...context.start_line ? { start_line: context.start_line, end_line: context.end_line } : {} } : saved.context || null;
  if (context) controls.prompt.value = `Regarding ${context.project}/${context.path} at revision ${context.revision}${context.start_line ? ` lines ${context.start_line}\u2013${context.end_line}` : ""}:

${controls.prompt.value}`;
  return { request_id: saved.request_id || crypto.randomUUID(), context: reference, uploads: saved.uploads || [] };
}
function composerPayload(controls, draft2) {
  return { ...draft2, uploads: (draft2.uploads || []).map(({ id, revision: revision2 }) => ({ id, revision: revision2 })), ...Object.fromEntries(Object.entries(controls).map(([key, node]) => [key, node.value])) };
}
async function taskDetail(id) {
  rememberDetail("task", id);
  const data = await api(`/api/tasks/detail?id=${id}`);
  const body = sheet(data.task.title);
  body.dataset.taskId = id;
  const viewToken = crypto.randomUUID();
  body.dataset.taskView = viewToken;
  body.append(el("p", "muted", `${data.specification.engine} \xB7 ${data.specification.profile} \xB7 ${data.specification.project.name}`));
  const state = el("p", "pill", data.flights[0]?.state || data.task.state);
  body.append(state);
  const initial = el("details", "card");
  initial.append(el("summary", "", "Original request"), el("p", "", data.specification.prompt));
  showAttachments(initial, data.specification.attachments);
  body.append(initial);
  const turns = section(body, "Conversation");
  const live = el("div", "reading");
  turns.append(live);
  const prompt = field(body, "Continue the conversation", el("textarea"));
  prompt.placeholder = "A follow-up, correction, or next step\u2026";
  prompt.value = localStorage.getItem("office-task-draft:" + id) || "";
  prompt.addEventListener("input", () => localStorage.setItem("office-task-draft:" + id, prompt.value));
  const actions = el("div", "actions");
  const uploadKey = "office-task-uploads:" + id;
  const attached = attachments(body, JSON.parse(localStorage.getItem(uploadKey) || "[]"), (items) => localStorage.setItem(uploadKey, JSON.stringify(items)));
  actions.append(replyButton(id, prompt, attached));
  const flightControls = el("div", "actions");
  actions.append(flightControls);
  updateFlightControls(flightControls, id, data.flights[0]);
  body.append(actions);
  let cursor = -1;
  const earlier = earlierTaskMessages(body, turns, id);
  async function poll() {
    if (!$("#detail").open || body.dataset.taskId !== id || body.dataset.taskView !== viewToken) return;
    try {
      const events = await api(`/api/tasks/history?id=${id}&cursor=${cursor}`);
      if (cursor === -1) {
        earlier.dataset.cursor = events.previous_cursor;
        earlier.hidden = events.previous_cursor === null;
      }
      for (const event of events.items) {
        cursor = event.id;
        renderEvent(turns, live, state, event, id);
      }
      if (cursor === -1) cursor = 0;
      const current2 = await api(`/api/tasks/detail?id=${id}`);
      if (body.dataset.taskView === viewToken) updateFlightControls(flightControls, id, current2.flights[0]);
    } catch (error) {
      state.textContent = error.message;
    }
    if ($("#detail").open && body.dataset.taskId === id && body.dataset.taskView === viewToken) setTimeout(poll, 1200);
  }
  earlier.classList.add("transcript-earlier");
  transcriptNavigation(body, turns).append(earlier);
  await poll();
}
function earlierTaskMessages(body, turns, id) {
  const earlier = button("Load earlier messages", async () => {
    const data = await api(`/api/tasks/history?id=${id}&cursor=${earlier.dataset.cursor}`);
    const group = el("div", "stack"), live = el("div", "reading"), state = el("p");
    group.append(live);
    for (const event of data.items) renderEvent(group, live, state, event, id);
    const height = body.scrollHeight;
    turns.prepend(...group.childNodes);
    body.scrollTop += body.scrollHeight - height;
    earlier.dataset.cursor = data.previous_cursor;
    earlier.hidden = data.previous_cursor === null;
  });
  earlier.hidden = true;
  turns.parentElement.insertBefore(earlier, turns);
  return earlier;
}
function replyButton(id, prompt, attached) {
  const key = "office-task-pending:" + id;
  return button("Send", async () => {
    let pending2 = JSON.parse(localStorage.getItem(key) || "null");
    if (!pending2) {
      if (!prompt.value.trim()) throw Error("Write a message first.");
      if (!attached.ready()) throw Error("Wait for the attachment upload to finish.");
      pending2 = { task_id: id, request_id: crypto.randomUUID(), text: prompt.value, uploads: attached.references() };
      localStorage.setItem(key, JSON.stringify(pending2));
    }
    try {
      await api("/api/tasks/say", pending2);
    } catch (error) {
      if ([400, 403, 404, 409, 413, 422].includes(error.status)) localStorage.removeItem(key);
      throw error;
    }
    localStorage.removeItem(key);
    if (attached.ready() && JSON.stringify(attached.references()) === JSON.stringify(pending2.uploads || [])) attached.clear();
    if (prompt.value === pending2.text) {
      prompt.value = "";
      localStorage.removeItem("office-task-draft:" + id);
    }
    notice("Queued on your Mac. Delivery status appears in the conversation.");
  }, "primary");
}
function updateFlightControls(parent, id, latest) {
  const version = latest ? `${latest.id}:${latest.state}` : "";
  if (parent.dataset.version === version) return;
  parent.dataset.version = version;
  parent.replaceChildren();
  if (!latest) return;
  parent.append(button("Outputs & changes", () => document.dispatchEvent(new CustomEvent("office-flight-detail", { detail: latest.id }))));
  if (latest.state !== "running") return;
  for (const [action, label] of [["interrupt", "Interrupt turn"], ["close", "Close session"]]) parent.append(button(label, async () => {
    await api("/api/tasks/control", { task_id: id, flight_id: latest.id, request_id: crypto.randomUUID(), action });
    notice("Control queued for this attempt.");
  }));
}
function renderEvent(turns, live, state, event, taskId) {
  const payload = event.payload;
  const renderers = {
    "office.phase": () => state.textContent = payload.state,
    "office.message": () => {
      if (!payload.initial) {
        const node = card("You", payload.text);
        showAttachments(node, payload.attachments);
        turns.insertBefore(node, live);
      }
    },
    "office.delivery": () => turns.insertBefore(el("p", "muted", `Message ${payload.message_id}: ${payload.state}${payload.consumption === "unknown" ? " \xB7 provider acknowledged; consumption unconfirmed" : ""}`), live),
    "office.permission_closed": () => {
      const node = turns.querySelector(`[data-permission-id="${payload.permission_id}"]`);
      if (node) node.replaceChildren(el("p", "muted", "Permission answered"));
    },
    "office.permission": () => turns.insertBefore(permissionCard({ id: event.id, task_id: taskId, payload }), live),
    "office.provider": () => provider(turns, live, payload),
    "office.session_closed": () => state.textContent = "Session closed; output retained",
    "office.unsupported_request": () => turns.insertBefore(card("Engine request needs support", JSON.stringify(payload.params)), live)
  };
  renderers[event.kind]?.();
}
function provider(turns, live, payload) {
  const method = payload.method, params = payload.params || {};
  if (method === "item/agentMessage/delta") {
    live.textContent += params.delta || "";
    return;
  }
  if (method === "claude/StreamEvent") {
    const delta = params.event?.delta;
    if (delta?.type === "text_delta") live.textContent += delta.text;
    return;
  }
  if (method === "item/completed") {
    renderItem(turns, live, params.item || {});
    return;
  }
  if (method === "claude/AssistantMessage") {
    const text = (params.content || []).filter((b) => b.text).map((b) => b.text).join("\n");
    if (text) {
      live.textContent = "";
      turns.insertBefore(markdownView(text), live);
    }
  }
}
function renderItem(turns, live, item) {
  if (item.type === "agentMessage") {
    live.textContent = "";
    turns.insertBefore(markdownView(item.text), live);
    return;
  }
  const details = el("details", "card");
  details.append(el("summary", "", item.type || "Engine event"), el("pre", "", JSON.stringify(item, null, 2)));
  turns.insertBefore(details, live);
}
function permissionCard(request) {
  const payload = request.payload;
  const params = payload.params || {};
  if (payload.method === "item/tool/requestUserInput" || params.tool === "AskUserQuestion") return inputRequest(request);
  const node = el("article", "card");
  node.append(el("h3", "", params.title || params.reason || "Permission requested"), el("pre", "", JSON.stringify(params, null, 2)));
  const controls = el("div", "actions");
  const requestId = crypto.randomUUID();
  for (const [decision, label] of [["accept", "Allow once"], ["decline", "Deny"]]) controls.append(button(label, async () => {
    await api("/api/tasks/answer", { task_id: request.task_id, permission_id: request.id, decision, request_id: requestId });
    controls.replaceChildren(el("p", "muted", "Answer queued for this exact request."));
  }));
  node.dataset.permissionId = String(request.id);
  node.append(controls);
  return node;
}
function inputRequest(request) {
  const params = request.payload.params;
  const questions = params.questions || params.input.questions;
  const node = el("article", "card");
  node.dataset.permissionId = String(request.id);
  const fields = /* @__PURE__ */ new Map();
  for (const question of questions) {
    const control = el("input");
    control.type = question.isSecret ? "password" : "text";
    const label = question.question;
    field(node, label, control);
    fields.set(question.id || label, control);
    if (question.options) {
      const options = el("div", "actions");
      for (const option of question.options) options.append(button(option.label, () => control.value = option.label));
      node.append(options);
    }
  }
  const requestId = crypto.randomUUID();
  node.append(button("Send answers", async () => {
    const answers = Object.fromEntries([...fields].map(([key, node2]) => [key, node2.value]));
    await api("/api/tasks/answer", { task_id: request.task_id, permission_id: request.id, decision: "answer", answers, request_id: requestId });
    node.replaceChildren(el("p", "muted", "Answers queued for this exact request."));
  }));
  return node;
}
function showAttachments(parent, items = []) {
  for (const item of items) {
    if (item.upload_id) {
      parent.append(link(item.source, `/api/uploads/content?id=${encodeURIComponent(item.upload_id)}&revision=${item.revision}`));
      continue;
    }
    if (!item.object_id) continue;
    const context = el("details", "card");
    context.append(el("summary", "", item.source || "Attached context"), el("p", "muted", `Attached snapshot \xB7 ${item.revision}`), el("pre", "", item.text || ""));
    context.append(button("Open current source", async () => {
      const { openFile: openFile2 } = await Promise.resolve().then(() => (init_office_files(), office_files_exports));
      await openFile2(item.object_id);
    }));
    parent.append(context);
  }
}
async function loadSources(controls, body) {
  const project2 = controls.project.value;
  const picker = controls.source_ref;
  const saved = picker.dataset.savedValue || picker.value || "HEAD";
  picker.disabled = true;
  const token2 = crypto.randomUUID();
  picker.dataset.request = token2;
  delete picker.dataset.error;
  try {
    const data = await api("/api/tasks/sources?project=" + encodeURIComponent(project2));
    if (!sourceCurrent(controls, project2, token2)) return;
    const options = [["HEAD", "Current checkout \xB7 " + data.revision.slice(0, 12)], ...data.items.map((item) => [item.id, item.label])];
    picker.replaceChildren(...select(options, "HEAD").children);
    picker.value = options.some(([id]) => id === saved) ? saved : "HEAD";
    picker.disabled = false;
    delete picker.dataset.savedValue;
    picker.dispatchEvent(new Event("input", { bubbles: true }));
    let note = body.querySelector("[data-source-note]");
    if (!note) {
      note = el("p", "muted");
      note.dataset.sourceNote = "true";
      picker.closest("label").after(note);
    }
    note.textContent = data.dirty ? "This checkout has uncommitted changes. The task starts from the selected saved commit." : "The task starts from the selected saved commit in an isolated folder.";
  } catch (error) {
    if (sourceCurrent(controls, project2, token2)) {
      picker.dataset.error = error.message;
      notice(error.message);
    }
  }
}
function sourceCurrent(controls, project2, token2) {
  return controls.project.value === project2 && controls.source_ref.dataset.request === token2 && controls.source_ref.isConnected;
}

// client/phone/office.js
var attention = { items: [], errors: [], failures: [] };
var snapshot = null;
var snapshotReadAt = 0;
async function world() {
  if (!snapshot || Date.now() - snapshotReadAt > 15e3) {
    const data = await api("/api/world");
    snapshot = data.world;
    snapshotReadAt = Date.now();
  }
  return snapshot;
}
async function guarded(parent, work2) {
  try {
    await work2();
  } catch (error) {
    failure(parent, error);
  }
}
async function today(parent) {
  intro(parent, "Your office, wherever you are", "A little room to think.", "The Mac holds the work. Everything you need to see and steer lives here.");
  const reports = section(parent, "Daily reports");
  await guarded(reports, () => dailyReports(reports));
  const coords = section(parent, "Coordinators");
  await guarded(coords, async () => {
    const data = await api("/api/coordinators");
    for (const row of data.coordinators) {
      const node = button("", () => {
        location.hash = "coordinator";
      }, "card coord-row");
      node.dataset.id = row.id;
      node.addEventListener("click", () => {
        try {
          localStorage.setItem("office-coordinator-pick", row.id);
        } catch {
        }
      }, { capture: true });
      node.append(el("h3", "", row.name), healthLine(row));
      coords.append(node);
    }
  });
  const needs = section(parent, "Needs you");
  needs.id = "needs-list";
  await guarded(needs, () => attentionList(needs));
  const active = section(parent, "Running now");
  await runningNow(active);
  const recent = section(parent, "Since you were here");
  await guarded(recent, () => podcastNotifications(recent));
  await guarded(recent, () => activitySinceVisit(recent));
  const listen = section(parent, "Something to listen to");
  await guarded(listen, async () => {
    const data = await api("/api/media?kind=podcast");
    const episode = data.items[0];
    if (episode) listen.append(card(episode.title, `${Math.round(episode.duration_s / 60)} minutes \xB7 ${episode.date}`, () => mediaDetail(episode.id)));
    else empty(listen, "Your next episode will appear here when published.");
  });
  const poem = section(parent, "A small interruption");
  await guarded(poem, async () => {
    const data = await api("/api/media?kind=substrate");
    const item = data.items[0];
    if (item) poem.append(card(item.title, item.excerpt, () => mediaDetail(item.id)));
    else empty(poem, "No Substrate pieces published yet.");
  });
}
function reportLead(text) {
  const lines = text.split("\n").map((line) => line.replace(/[*_#>|`]/g, "").trim()).filter(Boolean);
  return lines.find((line) => line.length > 40 && line !== line.toUpperCase()) || lines[0] || "";
}
async function dailyReports(parent) {
  const data = await api("/api/reports");
  for (const row of data.reports) {
    const node = el("details", "card report");
    const summary = el("summary");
    const when = row.at ? new Date(row.at).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "no report yet";
    summary.append(el("strong", "", row.name), el("span", "muted", ` \xB7 ${when}${row.stale ? " \xB7 showing the last one that loaded" : ""}${row.ok === false ? " \xB7 failed" : ""}`), el("p", "report-lead", reportLead(row.text || row.error || "")));
    node.append(summary);
    if (row.text) node.append(markdownView(row.text));
    parent.append(node);
  }
}
async function work(parent) {
  intro(parent, "Work", "Pick up the thread.", "Projects, conversations, and the files behind them.");
  const active = section(parent, "Running now");
  await runningNow(active);
  const history2 = section(parent, "Office conversations");
  await guarded(history2, () => taskList(history2));
  const bots = section(parent, "Your office voices");
  bots.append(button("Open office voices", () => guarded(bots, async () => {
    const data = await api("/api/bots");
    bots.replaceChildren();
    for (const bot of data.bots) bots.append(card(bot.name, bot.purpose, () => botConversation(bot)));
  })));
  const archive = section(parent, "All retained conversations");
  archive.append(button("Browse all retained conversations", () => archives(archive)));
  const projects = section(parent, "Projects");
  parent.insertBefore(projects.parentElement, active.parentElement);
  await guarded(projects, () => projectRoster(projects));
}
async function projectRoster(parent) {
  const loading = el("p", "muted", "Loading projects from your Mac\u2026");
  parent.append(loading);
  const [data, local] = await Promise.all([world(), api("/api/projects")]);
  loading.remove();
  const desks = projectDesks(data, local.items);
  const filter = el("input");
  filter.type = "search";
  filter.placeholder = "Filter projects";
  filter.setAttribute("aria-label", "Filter projects");
  parent.append(filter);
  const rows = el("div", "grid");
  parent.append(rows);
  let limit = 8;
  function draw() {
    rows.replaceChildren();
    const matches = desks.filter((desk) => (desk.repo + " " + (desk.label || "")).toLowerCase().includes(filter.value.toLowerCase()));
    for (const desk of matches.slice(0, limit)) rows.append(card(desk.repo, desk.label || desk.detail || desk.outcome, () => project(desk)));
    if (matches.length > limit) rows.append(button(`Show all ${matches.length} projects`, () => {
      limit = matches.length;
      draw();
    }));
  }
  filter.addEventListener("input", draw);
  draw();
  const inactive = (data.stations || []).filter((desk) => desk.hidden);
  if (inactive.length) {
    const away = el("details", "find-group");
    away.append(el("summary", "", `Inactive desks (${inactive.length})`));
    for (const desk of inactive) away.append(button(`Bring back ${desk.repo}`, () => setDeskHidden(desk.repo, false)));
    parent.append(away);
  }
}
async function setDeskHidden(repo, hidden) {
  const result = await api("/api/desks", { repo, hidden });
  if (!result.ok) throw Error("Desk change was not saved");
  snapshot = null;
  snapshotReadAt = 0;
  if ($("#detail").open) $("#detail").close();
  notice(hidden ? `${repo} put away` : `${repo} is active again`);
  await route();
}
function projectDesks(data, local) {
  const stations = data.stations || [];
  const hidden = new Set(stations.filter((desk) => desk.hidden).map((desk) => desk.repo.toLowerCase()));
  const rows = local.filter((root) => !hidden.has(root.name.toLowerCase())).map((root) => ({ ...stations.find((desk) => desk.repo === root.name), repo: root.name, root: root.id, folder: root.folder_id || btoa(JSON.stringify([root.id, ""])).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, ""), checkoutPath: root.path, taskCapable: root.task_capable !== false, label: root.label }));
  for (const desk of stations) if (!desk.hidden && !local.some((root) => root.name === desk.repo)) rows.push(desk);
  return rows;
}
async function project(desk) {
  rememberDetail("project", JSON.stringify({ repo: desk.repo, root: desk.root }));
  const body = sheet(desk.repo);
  body.append(el("p", "muted", desk.label || desk.detail || ""));
  if (desk.hidden === false) body.append(button("Put away from active desks", () => setDeskHidden(desk.repo, true)));
  if (desk.root && desk.taskCapable) body.append(button("New task in this checkout", () => newTask(desk.root), "primary"));
  if (desk.root && !desk.taskCapable) body.append(el("p", "muted", "This is a file collection. New tasks require a Git checkout so their work can run in an isolated clone."));
  const nav = el("div", "actions");
  const view = el("div");
  body.append(nav, view);
  if (desk.root) nav.append(button("Files", () => projectPanel(view, (pane) => browse(pane, desk.folder))), button("Conversations", () => projectPanel(view, (pane) => projectTasks(pane, desk))), button("Agents", () => projectPanel(view, (pane) => projectAgents(pane, desk))), button("Outputs", () => projectPanel(view, (pane) => projectTasks(pane, desk, true))));
  if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(desk.repo)) {
    nav.append(button("GitHub files", () => githubTree(desk.repo)), button("Issues & pull requests", () => projectPanel(view, (pane) => projectWork(pane, desk))));
    projectWork(view, desk);
  } else if (desk.root) await projectPanel(view, (pane) => browse(pane, desk.folder));
}
function projectWork(parent, desk) {
  parent.replaceChildren();
  parent.append(button("New issue", () => createIssue(desk.repo)));
  for (const [key, title] of [["issues", "Issues"], ["prs", "Pull requests"]]) {
    const list = section(parent, title);
    for (const item of desk[key] || []) list.append(card(`#${item.number} ${item.title}`, item.state || "", () => githubDetail(desk.repo, item, key)));
    if (!desk[key]?.length) empty(list, "None in the current snapshot.");
    list.append(button("Browse all open and closed", () => githubCollection(desk.repo, key)));
  }
}
async function githubDetail(repo, item, kind) {
  rememberDetail("github", JSON.stringify({ repo, number: item.number, kind }));
  const body = sheet(`${repo} #${item.number}`);
  await guarded(body, async () => {
    const query = `repo=${encodeURIComponent(repo)}&number=${item.number}&kind=${kind}`;
    const data = await api(`/api/github/detail?${query}`);
    if (kind !== "prs" && data.pull_request) return githubDetail(repo, item, "prs");
    body.append(el("h2", "", data.title), el("p", "muted", `${data.state} \xB7 ${data.acting_identity} \xB7 observed ${new Date(data.observed_at * 1e3).toLocaleString()}`), markdownView(data.body || ""), link("Open on GitHub", data.html_url));
    if (kind === "prs") {
      body.append(el("p", "muted", `Head ${data.head.sha}`));
      const checks = section(body, "Checks");
      await githubChecks(checks, repo, data.head.sha);
      const changes2 = section(body, "Changed files");
      githubFiles(changes2, repo, item.number, data.files, data.head.sha);
      if (data.files_next_cursor) changes2.append(button("More changed files", () => githubFilePage(changes2, repo, item.number, data.files_next_cursor, data.head.sha)));
      body.append(button("Ask an agent about this change", () => githubContext({ kind: "change", repo, number: item.number, head: data.head.sha, base: data.base.sha })), button("Reviews & inline discussion", () => githubReviews(repo, item.number)), button("Review this change", () => reviewChange(repo, item.number, data.head.sha)), button("Merge checked pipeline change", () => githubAction({ action: "merge", repo, number: item.number, head: data.head.sha })));
      body.append(el("p", "muted", "Merge requires a pipeline branch, the current head, a passing verify check, and permission under GitHub\u2019s merge rules."));
      githubDiff(body, repo, item.number, data);
    }
    body.append(button("Full timeline", () => githubTimeline(repo, item.number)));
    body.append(button("Edit labels", () => editLabels(repo, item.number, data.labels || [])));
    const comments = section(body, "Discussion");
    for (const comment of data.comments || []) comments.append(commentView(comment));
    if (data.comments_next_cursor) comments.append(button("More comments", () => githubComments(comments, repo, item.number, data.comments_next_cursor)));
    const reply = field(body, "Reply", el("textarea"));
    const clearReply = restoreDraft(reply, ["github", repo, item.number]);
    body.append(button("Post reply", async () => {
      await githubAction({ action: "comment", repo, number: item.number, body: reply.value }, (committed) => clearReply(committed.body));
    }));
    if (kind === "issues") body.append(button(data.state === "closed" ? "Reopen issue" : "Close issue", async () => {
      await githubAction({ action: data.state === "closed" ? "reopen" : "close", repo, number: item.number });
    }));
  });
}
function commentView(comment) {
  const node = el("article", "card");
  node.append(el("h3", "", comment.user?.login || "Comment"), markdownView(comment.body || ""));
  return node;
}
async function githubComments(parent, repo, number, cursor) {
  const data = await api(`/api/github/comments?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}`);
  for (const item of data.items) parent.append(commentView(item));
  if (data.next_cursor) parent.append(button("More comments", () => githubComments(parent, repo, number, data.next_cursor)));
}
function githubFiles(parent, repo, number, files, sha) {
  for (const file of files || []) {
    const details = el("details", "card");
    details.append(el("summary", "", `${file.filename} \xB7 +${file.additions} \u2212${file.deletions}`), el("pre", "", file.patch || "GitHub omitted this patch; open the full file."), button("Read file at this head", () => githubTree(repo, file.filename, sha)));
    parent.append(details);
  }
}
async function githubFilePage(parent, repo, number, cursor, sha) {
  const data = await api(`/api/github/files?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}&head=${sha}`);
  githubFiles(parent, repo, number, data.items, sha);
  if (data.next_cursor) parent.append(button("More changed files", () => githubFilePage(parent, repo, number, data.next_cursor, sha)));
}
async function githubTree(repo, path = "", ref = "HEAD") {
  const host = sheet(repo + " / " + path);
  const body = el("div");
  host.append(body);
  await guarded(body, async () => {
    const data = await api(`/api/github/tree?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`);
    if (!body.isConnected) return;
    const revision2 = data.ref || ref;
    rememberDetail("github-tree", JSON.stringify({ repo, path, ref: revision2 }));
    body.append(el("p", "muted", data.source), button("Choose branch", () => githubBranches(repo)));
    if (path) body.append(button("Parent folder", () => githubTree(repo, path.split("/").slice(0, -1).join("/"), revision2)));
    if (data.items) {
      for (const item of data.items) body.append(card(item.name, item.type, () => githubTree(repo, item.path, revision2)));
    } else {
      body.append(el("p", "muted", `Blob ${data.object.sha}`), el("pre", "", data.object.text));
      if (data.object.readable) {
        body.append(button("Ask an agent about this file", () => githubContext({ kind: "file", repo, path, ref: revision2, blob: data.object.sha })));
        githubLineSelection(body, { kind: "file", repo, path, ref: revision2, blob: data.object.sha }, data.object.text);
      }
      if (data.object.html_url) body.append(link("Open source", data.object.html_url));
    }
  });
}
async function githubBranches(repo, cursor = 1, parent = null) {
  const body = parent || el("div");
  if (!parent) sheet(repo + " \xB7 branches").append(body);
  let loaded = false;
  if (!parent) body.append(el("p", "muted", "Browse the saved commit on GitHub. Your Mac checkout stays on its current branch."));
  await guarded(body, async () => {
    const data = await api(`/api/github/branches?repo=${encodeURIComponent(repo)}&cursor=${cursor}`);
    if (!body.isConnected) return;
    for (const branch of data.items) body.append(card(branch.name, branch.sha.slice(0, 12), () => githubTree(repo, "", branch.sha)));
    if (data.next_cursor) {
      const more = button("More branches", async () => {
        more.disabled = true;
        if (await githubBranches(repo, data.next_cursor, body)) more.remove();
        else more.disabled = false;
      });
      body.append(more);
    }
    loaded = true;
  });
  return loaded;
}
async function refreshRunningSource(source) {
  if (source.busy) return;
  source.busy = true;
  const next = el("div");
  try {
    await source.render(next);
    source.rows.replaceChildren(...next.childNodes);
    source.status.textContent = `Checked ${(/* @__PURE__ */ new Date()).toLocaleTimeString()}`;
  } catch (error) {
    source.status.textContent = `${source.name} unavailable; last view retained. ${error.message}`;
  } finally {
    source.busy = false;
  }
}
async function observedProcesses(parent) {
  const data = await api("/api/live");
  if (data.state === "unreadable") throw Error(data.detail);
  if (data.detail) parent.append(el("p", "error", data.detail));
  parent.append(el("p", "muted", `${data.sessions.length} Mac processes \xB7 observed separately`));
  for (const item of data.sessions) parent.append(card(`${item.engine} \xB7 PID ${item.pid}`, `${item.cwd || "Folder unavailable"} \xB7 process observed`, () => observedProcess(item)));
}
async function runningNow(parent) {
  const sources = [
    { name: "Office tasks", render: (node) => taskList(node, true) },
    { name: "Agent sessions", render: (node) => sessions(node, true) },
    { name: "Mac processes", render: observedProcesses, collapsed: true }
  ];
  for (const source of sources) {
    const container = el(source.collapsed ? "details" : "div");
    if (source.collapsed) container.append(el("summary", "", "Observed Mac processes"));
    source.status = el("p", "muted");
    source.rows = el("div");
    container.append(source.status, source.rows);
    parent.append(container);
  }
  function refresh() {
    if (!parent.isConnected) return;
    for (const source of sources) void refreshRunningSource(source);
  }
  refresh();
  const timer = setInterval(() => {
    if (!parent.isConnected) clearInterval(timer);
    else refresh();
  }, 1e4);
}
function observedProcess(item) {
  const body = sheet(`${item.engine} \xB7 PID ${item.pid}`);
  body.append(el("p", "", item.cwd), el("p", "muted", `Observed process; started ${item.started || "unknown"}. A process can host multiple conversations. Account and control are not inferred from its folder.`));
  if (item.transcript) body.append(button("Read exact open transcript", () => observedTranscript(body, item.key)));
  else body.append(el("p", "muted", "No unique open transcript is proven. Retained histories remain available in Work."));
}
async function observedTranscript(body, key) {
  const data = await api(`/api/live/transcript?key=${encodeURIComponent(key)}&offset=-1&limit=100`);
  const turns = el("div", "stack");
  const render = (parent, lines) => {
    for (const line of lines) parent.append(card(line.who || line.kind, line.text));
  };
  const earlier = button("Load earlier messages", async () => {
    const end = Number(earlier.dataset.offset), offset = Math.max(0, end - 100);
    const page = await api(`/api/live/transcript?key=${encodeURIComponent(key)}&offset=${offset}&limit=${end - offset}&identity=${encodeURIComponent(data.identity)}`);
    const fragment = el("div");
    render(fragment, page.lines);
    const height = body.scrollHeight;
    turns.prepend(...fragment.childNodes);
    body.scrollTop += body.scrollHeight - height;
    earlier.dataset.offset = offset;
    earlier.hidden = offset === 0;
  });
  earlier.dataset.offset = data.offset;
  earlier.hidden = data.offset === 0;
  body.append(earlier, turns);
  render(turns, data.lines);
  earlier.classList.add("transcript-earlier");
  transcriptNavigation(body, turns).append(earlier);
}
async function sessions(parent, onlyActive = false) {
  const data = await api("/api/sessions");
  if (!["ok", "empty"].includes(data.state)) throw Error(data.detail || data.state);
  const rows = data.sessions.filter((item) => !onlyActive || item.status !== "inactive");
  for (const item of rows) parent.append(card(item.name, `${item.tool} \xB7 hcom reports ${item.status} \xB7 ${item.repo || item.directory}`, () => conversation2(item)));
  if (!rows.length) empty(parent, "No addressable sessions currently active.");
}
async function conversation2(session) {
  rememberDetail("hcom", JSON.stringify({ session_id: session.session_id, name: session.name, started_at: session.started_at, directory: session.directory }));
  const body = el("div");
  sheet(`${session.name} \xB7 ${session.tool}`).append(body);
  body.append(el("p", "muted", session.directory));
  const data = await api(`/api/session?name=${encodeURIComponent(session.name)}&last=50`);
  if (!body.isConnected) return;
  const turns = el("div", "stack");
  body.append(turns);
  for (const turn of data.exchanges) {
    if (turn.you) turns.append(card("You", turn.you));
    if (turn.them) turns.append(markdownView(turn.them));
  }
  transcriptNavigation(body, turns);
  const message = field(body, "Steer this session", el("textarea"));
  message.placeholder = "A follow-up, correction, or next step\u2026";
  const clearMessage = restoreDraft(message, ["hcom", session.session_id || [session.name, session.started_at, session.directory]]);
  const send = button("Send message", async () => {
    const sent = message.value;
    const result = await api("/api/session/say", { name: session.name, text: sent });
    notice(result.result || "Queued for the agent; delivery is not yet confirmed.");
    clearMessage(sent);
  });
  send.disabled = !session.reachable;
  body.append(send);
}
async function library(parent) {
  intro(parent, "Library", "Everything has a place.", "Read, listen, browse. Follow a file back to the work that made it.");
  parent.append(link("Lesson previews", "/lessons"));
  parent.append(link("Lesson outlines", "/outlines"));
  const filters = el("div", "actions");
  const view = el("div");
  parent.append(filters, view);
  const show = async (kind) => {
    const content = el("div");
    view.replaceChildren(content);
    if (kind === "files") await browse(content);
    else await mediaList(content, kind);
  };
  for (const [key, label] of [["files", "Files & documents"], ["podcast", "Podcasts"], ["substrate", "Substrate"]]) filters.append(button(label, () => show(key)));
  for (const [key, title] of [["saved", "Saved"], ["recent", "Recently opened"]]) {
    const group = el("details", "card");
    group.append(el("summary", "", title));
    for (const item of collection(key).slice(0, 20)) group.append(card(item.title || item.kind, "Open saved view", () => restoreDetail(item)));
    parent.insertBefore(group, view);
  }
  await show("files");
  await officeSections(parent, ["library", "mail", "care", "money_swarm"]);
}
async function system(parent) {
  intro(parent, "System", "Behind the scenes.", "Connection, schedules, and the complete run history.");
  const health = section(parent, "Your Mac");
  await guarded(health, async () => {
    const data = await api("/api/health");
    health.append(card(data.ok ? "Connected" : "Needs attention", `Serving ${data.revision?.slice(0, 10)} \xB7 snapshot ${data.snapshot_at}`));
  });
  await guarded(health, async () => {
    const data = await api("/api/system/machine");
    health.append(card("Machine", data.uptime), card("Memory", data.memory), card("Storage", `${(data.disk.free / 1073741824).toFixed(1)} GB free`));
  });
  const observed = section(parent, "Other Mac agent processes");
  await guarded(observed, async () => {
    const data = await api("/api/live");
    observed.append(el("p", "muted", `Observed ${data.as_of}. Process visibility does not prove account or conversation identity.`));
    for (const item of data.sessions) observed.append(card(`${item.engine} \xB7 PID ${item.pid}`, `${item.cwd} \xB7 observed only`, () => {
      const body = sheet("Observed Mac process");
      body.append(el("p", "", `PID ${item.pid} \xB7 started ${item.started}`), el("p", "muted", "Control and transcript association are unproven. Open an exact retained conversation in Work."));
    }));
  });
  const scheduleBox = el("details");
  scheduleBox.append(el("summary", "", "Machine schedules and services"));
  parent.append(scheduleBox);
  const existing = section(scheduleBox, "Registered jobs");
  await guarded(existing, async () => {
    const data = await api("/api/system/jobs");
    if (data.state !== "ok") existing.append(el("p", "error", data.detail || data.state));
    for (const job of data.jobs || []) existing.append(card(job.id, `${job.state} \xB7 ${job.schedule}`, () => {
      const body = sheet(job.id);
      body.append(el("p", "", job.detail), el("p", "", `Last attempt: ${job.last_attempt || "none"} \xB7 exit ${job.last_rc ?? "unknown"}`), el("pre", "", job.command), el("p", "muted", job.note), button("Read log", () => jobLog(job.id)), button("Run receipts", () => jobReceipts(job.id)));
    }));
  });
  const plans = section(parent, "Nexus automations");
  await guarded(plans, async () => {
    const data = await api("/api/system/plans");
    for (const plan of data.items) plans.append(card(plan.name, `${plan.enabled ? "Enabled" : "Paused"} \xB7 ${plan.kind}`, () => planDetail(plan)));
  });
  const history2 = section(parent, "Run history");
  await guarded(history2, () => runs(history2));
  await officeSections(parent, ["flows", "cost", "webhook", "care-grader"]);
  parent.append(button("Settings", settings));
}
async function runs(parent, cursor = 0) {
  const data = await api(`/api/system/runs?cursor=${cursor}`);
  for (const item of data.items) parent.append(card(item.title || item.plan, `${item.state} \xB7 ${new Date(item.created_at * 1e3).toLocaleString()}`, () => flightDetail(item.id)));
  if (data.next_cursor !== null) parent.append(button("Older runs", () => runs(parent, data.next_cursor)));
}
async function flightDetail(id) {
  rememberDetail("flight", id);
  const data = await api(`/api/system/flight?id=${id}`);
  const body = sheet(data.title || id);
  body.append(el("p", "muted", `${data.state} \xB7 ${data.plan} \xB7 attempt ${data.attempt}`));
  body.append(markdownView(data.objective || ""));
  for (const action of ["failed", "cancelled"].includes(data.state) ? ["retry"] : ["running", "queued"].includes(data.state) ? ["cancel"] : []) body.append(button(action, () => systemCommand(action, id)));
  const log = section(body, "Log");
  await flightLogs(log, id);
  const events = section(body, "Events");
  await eventPage(events, id);
  const artifacts = section(body, "Artifacts");
  for (const item of data.artifacts) artifacts.append(card(item.kind, item.ref, () => {
    const view = sheet(item.ref);
    view.append(link("Open or download artifact", `/api/system/artifact?id=${encodeURIComponent(item.id)}`));
    const frame = el("iframe", "preview");
    frame.src = `/api/system/artifact?id=${encodeURIComponent(item.id)}`;
    frame.setAttribute("sandbox", "allow-scripts");
    frame.title = item.ref;
    view.append(frame);
  }));
}
async function flightLogs(parent, id) {
  const lanes = await api(`/api/system/log-lanes?id=${id}`);
  const log = el("div");
  const picker = select([["", "Main log"], ...lanes.items.map((name) => [name, name])], "");
  picker.setAttribute("aria-label", "Log source");
  picker.addEventListener("change", () => {
    log.replaceChildren();
    logPage(log, id, 0, picker.value).catch((error) => failure(log, error));
  });
  parent.append(picker, log);
  await logPage(log, id);
}
async function logPage(parent, id, offset = 0, lane = "", version = "") {
  const data = await api(`/api/system/log?id=${id}&offset=${offset}&lane=${encodeURIComponent(lane)}&version=${encodeURIComponent(version)}`);
  parent.append(el("p", "muted", data.state), el("pre", "", data.text || "No log produced yet."));
  if (data.next_offset !== null) parent.append(button(data.state === "rotated" ? "Read current log" : "Continue log", () => logPage(parent, id, data.next_offset, lane, data.version)));
}
async function eventPage(parent, id, cursor = 0) {
  const data = await api(`/api/system/events?id=${id}&cursor=${cursor}`);
  for (const item of data.items) parent.append(card(item.kind, JSON.stringify(item.payload)));
  if (data.next_cursor !== null) parent.append(button("More events", () => eventPage(parent, id, data.next_cursor)));
}
function planDetail(plan) {
  const body = sheet(plan.name);
  body.append(el("p", "", plan.objective || ""), el("p", "muted", `${plan.enabled ? "Enabled" : "Paused"} \xB7 ${plan.kind}`), el("pre", "", plan.schedule));
  for (const action of [plan.enabled ? "pause" : "resume", "run"]) body.append(button(action, () => systemCommand(action, plan.id)));
}
var searchKind = "";
async function search(cursor = 0) {
  const query = $("#global-search").value.trim();
  if (!query) return route();
  const parent = $("#content");
  if (cursor === 0) {
    parent.replaceChildren();
    intro(parent, "Everywhere", `\u201C${query}\u201D`, "Names, paths, and contents across your office.");
    const filters = el("div", "actions");
    for (const [kind, label] of [["", "All"], ["file", "Files"], ["conversation", "Conversations"], ["github", "GitHub"], ["event", "Events"], ["log", "Logs"], ["podcast", "Podcasts"], ["substrate", "Poetry"]]) filters.append(button(label, () => {
      searchKind = kind;
      search();
    }));
    parent.append(filters);
  }
  await guarded(parent, async () => {
    const data = await api(`/api/search/all?q=${encodeURIComponent(query)}&cursor=${cursor}&kind=${searchKind}`);
    parent.append(el("p", "muted", `${data.total || 0} results \xB7 index ${data.coverage.state} \xB7 ${data.coverage.count || 0} objects \xB7 updated ${data.coverage.finished_at ? new Date(data.coverage.finished_at * 1e3).toLocaleTimeString() : "never"}`));
    showSearchCoverage(parent, data.coverage);
    for (const problem of data.coverage.errors || []) parent.append(el("p", "error", `${problem.source}: ${problem.error}`));
    if (data.coverage.refresh?.state === "indexing") parent.append(button("Index updating \xB7 refresh results", () => search()));
    for (const item of data.items) parent.append(card(item.title, `${item.project} / ${item.path}
${item.excerpt}`, () => openSearchResult(item)));
    if (data.next_cursor !== null) parent.append(button("More results", () => search(data.next_cursor)));
  });
}
var decisionIndex = 0;
var feedCategory = "all";
async function watch(parent) {
  await refreshAttention();
  const rows = attention.items.filter(requiresYou);
  const failures = attention.failures;
  let coordinatorRows = [];
  let coordinatorError = "";
  try {
    const data = await api("/api/coordinators");
    coordinatorRows = data.coordinators || [];
  } catch (error) {
    coordinatorError = error.message;
  }
  const exceptions = coordinatorRows.filter((row) => row.thrashing || ["failing", "stalled", "error"].includes(row.health));
  const healthy = coordinatorRows.length - exceptions.length;
  const overview = el("header", "watch-overview");
  overview.append(el("div", "eyebrow", "Watch"));
  if (attention.errors.length) overview.append(el("h1", "", "Checking what needs you"));
  else if (rows.length) overview.append(el("h1", "", rows.length === 1 ? "One thing needs you" : `${rows.length} things need you`));
  else overview.append(el("h1", "", "Nothing needs you"));
  if (coordinatorError) overview.append(el("p", "watch-summary-muted", "I can\u2019t confirm system status right now."));
  else {
    const summary = [];
    if (healthy) summary.push(`${healthy} ${healthy === 1 ? "system is" : "systems are"} working normally`);
    if (exceptions.length) summary.push(`${exceptions.length} ${exceptions.length === 1 ? "needs" : "need"} attention`);
    if (!summary.length) summary.push("No systems are reporting a problem");
    overview.append(el("p", exceptions.length ? "watch-summary-attention" : "watch-summary-normal", summary.join(" \xB7 ")));
  }
  if (attention.errors.length) overview.append(el("p", "watch-summary-muted", "The decision checks are unavailable, so I can\u2019t confirm yet."));
  parent.append(overview);
  if (rows.length || attention.errors.length) {
    const decisions = section(parent, "Needs you");
    decisions.parentElement.classList.add("watch-decisions");
    if (rows.length) {
      decisionIndex = Math.max(0, Math.min(decisionIndex, rows.length - 1));
      const stack = el("div", "decision-stack");
      decisions.append(stack);
      const draw = () => {
        const entry = rows[decisionIndex];
        stack.replaceChildren();
        stack.append(el("p", "decision-count", `${decisionIndex + 1} of ${rows.length} decisions`));
        const nav = el("div", "decision-nav");
        const previous = button("\u2190 Previous", () => {
          decisionIndex = (decisionIndex - 1 + rows.length) % rows.length;
          draw();
        });
        const next = button("Next \u2192", () => {
          decisionIndex = (decisionIndex + 1) % rows.length;
          draw();
        });
        nav.append(previous, next, button("Full list", () => {
          const body = sheet("All decisions");
          for (const item of rows) body.append(item.kind === "permission" ? permissionCard(item.item) : attentionCard(item));
        }));
        stack.append(nav, entry.kind === "permission" ? permissionCard(entry.item) : attentionCard(entry));
      };
      draw();
    } else if (attention.errors.length) {
      decisions.append(el("p", "watch-unconfirmed", "I can't confirm yet. A source for decisions is unavailable."));
    }
    if (attention.errors.length) {
      const source = el("details", "watch-source-note");
      source.append(el("summary", "", "Check details"));
      for (const problem of attention.errors) source.append(el("p", "muted", problem));
      decisions.append(source);
    }
  }
  if (failures.length) {
    const stalled = section(parent, "Automation needs repair");
    stalled.append(el("p", "muted", `${failures.length} automated ${failures.length === 1 ? "pass needs" : "passes need"} diagnosis. Open the issue history for the original work and blocker before giving guidance.`));
    for (const entry of failures) stalled.append(automationFailureCard(entry));
    stalled.append(link("Track the repair", "https://github.com/ariaxhan/nexus-office/issues/186"));
  }
  if (coordinatorRows.length) {
    const systems = el("section", "watch-systems");
    systems.append(el("h2", "watch-section-title", "Systems"));
    for (const row of coordinatorRows) {
      const issue = exceptions.includes(row);
      const names = { tbs: "Thinking Brain School", matra: "Matra", office: "Office" };
      const outcomes = { tbs: "Keeping lessons healthy and ready for families.", matra: "Fixing app issues and delivering tested improvements.", office: "Moving Office issues, releases, failures and stabilization through Tower." };
      const item = el("article", "watch-system" + (issue ? " is-attention" : ""));
      const head = el("div", "watch-system-head");
      head.append(el("h3", "", names[row.id] || row.name), el("span", issue ? "watch-system-status is-attention" : "watch-system-status", issue ? "Needs attention" : row.health === "idle" ? "Idle" : row.health === "running" ? "Working" : "Working normally"));
      item.append(head, el("p", "watch-system-outcome", outcomes[row.id] || "Moving its assigned work forward."), el("p", "watch-system-action", row.id === "office" ? row.working_on || "No current Office work" : issue ? "I\u2019m looking into it." : "Nothing needed from you."));
      if (row.id === "office") item.append(el("p", "muted", `Last run ${row.age_s == null ? "never" : formatAge(row.age_s * 1e3)} \xB7 ${row.failures || 0} recent failures \xB7 ${row.changes || 0} recent changes \xB7 ${row.unread || 0} queued messages`));
      item.append(button(row.id === "office" ? "Inspect Office" : "See activity", () => {
        localStorage.setItem("office-coordinator-pick", row.id);
        location.hash = "coordinator";
      }, "watch-activity-link"));
      systems.append(item);
    }
    parent.append(systems);
  } else if (coordinatorError) {
    const systems = section(parent, "Systems");
    systems.append(el("p", "watch-summary-muted", "System status is unavailable."));
  }
  await guarded(parent, async () => {
    const done = coordinatorRows.flatMap((row) => (row.commits || []).map((item) => ({ ...item, checkout: item.checkout || row.id }))).filter((item) => item.sha && item.checkout).sort((a, b) => new Date(b.at || 0) - new Date(a.at || 0)).slice(0, 3);
    const recent = el("details", "watch-state watch-recent");
    const summary = el("summary");
    summary.append(el("strong", "", "Recent changes"));
    recent.append(summary);
    const list = el("div", "watch-done-list");
    for (const item of done) {
      const row = button("", () => commit(item.sha, item.checkout), "watch-done-item");
      const names = { tbs: "Thinking Brain School", "thinking-brain-school": "Thinking Brain School", matra: "Matra" };
      row.append(el("strong", "", item.subject || "Committed change"), el("span", "muted", `${names[item.checkout] || item.checkout} \xB7 ${item.at ? formatAge(Date.now() - new Date(item.at).getTime()) : "time unavailable"}`));
      list.append(row);
    }
    if (!done.length) list.append(el("p", "muted", coordinatorRows.length ? "No recent commit receipts." : "Commit evidence is unavailable right now."));
    recent.append(list);
    parent.append(recent);
  });
}
function requiresYou(entry) {
  return entry.kind === "permission" || entry.kind === "gate" || entry.kind === "issue" || entry.kind === "buzz";
}
function formatAge(milliseconds) {
  if (!Number.isFinite(milliseconds)) return "not checked yet";
  const minutes = Math.max(0, Math.floor(milliseconds / 6e4));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
function feedAction(label, active, fn, glyph) {
  const control = button("", fn, "feed-icon" + (active ? " active" : ""));
  control.innerHTML = glyph;
  control.title = label;
  control.setAttribute("aria-label", label);
  control.setAttribute("aria-pressed", String(active));
  return control;
}
async function feedDetail(post) {
  const data = await api("/api/feed/detail?id=" + encodeURIComponent(post.id));
  const body = sheet(data.title);
  body.append(el("p", "muted", `${data.category} \xB7 ${new Date(data.published_at * 1e3).toLocaleString()} \xB7 ${data.model}`), el("p", "", data.body));
  const sources = section(body, "Sources");
  for (const source of data.sources) {
    const issue = source.url.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(issues|pull)\/(\d+)/);
    if (source.url === "/#watch") sources.append(button(source.title, () => {
      document.querySelector("#detail").close();
      location.hash = "watch";
    }));
    else if (source.url.startsWith("/api/media/detail?id=")) {
      const id = decodeURIComponent(source.url.split("id=")[1]);
      sources.append(button(source.title, () => mediaDetail(id)));
    } else if (source.url.startsWith("/api/buzz/detail?id=")) {
      const id = decodeURIComponent(source.url.split("id=")[1]);
      sources.append(button(source.title, () => buzzSourceDetail(id)));
    } else if (issue) sources.append(button(source.title, () => githubDetail(issue[1], { number: Number(issue[3]) }, issue[2] === "pull" ? "prs" : "issues")));
    else sources.append(link(source.title, source.url));
  }
  if (data.media?.length) {
    const media = section(body, "Media");
    for (const asset of data.media) {
      if (asset.kind === "audio") {
        const player = el("audio");
        player.controls = true;
        player.src = asset.url;
        media.append(player, button("Episode details", () => mediaDetail(decodeURIComponent(asset.source_url.split("id=")[1]))));
        continue;
      }
      const image = el("img");
      image.src = asset.url || asset.source_url;
      image.alt = asset.alt;
      media.append(image, link("Original media", asset.source_url));
    }
  }
  const followBox = el("details", "find-group");
  followBox.append(el("summary", "", "Follow an evolving topic"));
  const followInput = el("input");
  followInput.placeholder = "e.g. Mercury, local models";
  followInput.setAttribute("aria-label", "Topic to follow");
  followBox.append(followInput, button("Follow", async () => {
    await api("/api/feed/follow", { label: followInput.value, active: true });
    notice("Following " + followInput.value);
    feedDetail(post);
  }));
  const followed = await api("/api/feed/threads");
  for (const item of followed.items) followBox.append(button("Following " + item.label + " \xB7 remove", async () => {
    await api("/api/feed/follow", { label: item.label, active: false });
    feedDetail(post);
  }));
  body.append(followBox);
  const replies = section(body, "Your replies");
  for (const reply of data.replies) replies.append(el("p", "", reply.body));
  const fieldNode = field(body, "Reply", el("textarea"));
  fieldNode.placeholder = "What did this miss or make you curious about?";
  body.append(button("Send reply", async () => {
    await api("/api/feed/reply", { id: post.id, body: fieldNode.value });
    notice("Reply saved");
    feedDetail(post);
  }, "primary"));
}
async function buzzSourceDetail(id) {
  const data = await api("/api/buzz/detail?id=" + encodeURIComponent(id));
  const body = sheet("Source conversation");
  for (const row of data.thread) {
    const state = ["posted", row.mirrored && "mirrored", row.coordinator_read && "coordinator read", row.acted && "acted on"].filter(Boolean).join(" \xB7 ");
    body.append(el("p", "muted", `${row.author} \xB7 #${row.channel} \xB7 ${new Date(row.at).toLocaleString()} \xB7 ${state}`), el("p", "", row.text));
    if (row.issue) {
      const [repo, number] = row.issue.split("#");
      body.append(link("Open linked issue", `https://github.com/${repo}/issues/${number}`));
    }
    if (row.receipt) body.append(el("p", "muted", `Receipt: ${row.receipt}`));
    body.append(el("p", "muted", `Buzz event: ${row.source}`));
  }
}
async function digestDetail(id) {
  const data = await api("/api/digests/detail?id=" + encodeURIComponent(id));
  const body = sheet(data.title + " \xB7 " + data.date);
  body.append(el("p", "muted", "Your original daily email \xB7 saved in Office"), el("div", "digest-copy", data.text));
  if (data.links.length) {
    const sources = el("details", "find-group");
    sources.append(el("summary", "", `${data.links.length} links in this email`));
    for (const item of data.links) sources.append(link(item.title, item.url));
    body.append(sources);
  }
}
async function dailyDigests(parent) {
  const data = await api("/api/digests");
  if (!data.items.length) return;
  const latest = data.items[0].date;
  const current2 = data.items.filter((item) => item.date === latest);
  const box = el("details", "digest-strip");
  box.append(el("summary", "", `Daily dispatches \xB7 ${latest} \xB7 ${current2.length} emails`));
  const list = el("div", "digest-list");
  for (const item of current2) list.append(button(item.title, () => digestDetail(item.id)));
  box.append(list);
  parent.append(box);
}
function renderFeedPost(parent, post) {
  const article = el("article", "feed-post");
  const kind = post.format === "paper" ? " \xB7 paper" : "";
  article.append(
    el("p", "feed-eyebrow", `${post.category}${kind} \xB7 ${new Date(post.published_at * 1e3).toLocaleString()}`),
    el("h2", "", post.title)
  );
  if (post.media?.length) {
    for (const asset of post.media) {
      if (asset.kind === "audio") {
        const player = el("audio", "feed-audio");
        player.controls = true;
        player.src = asset.url;
        article.append(player);
      } else if (asset.kind === "image") {
        const image = el("img", "feed-media");
        image.src = asset.url || asset.source_url;
        image.alt = asset.alt;
        article.append(image);
      }
    }
  }
  article.append(el("p", "", post.body));
  const actions = el("div", "feed-actions");
  article.append(actions);
  const drawActions = () => {
    actions.replaceChildren(button(`${post.sources.length} source${post.sources.length === 1 ? "" : "s"} \xB7 context \u2192`, () => feedDetail(post), "feed-source"));
    for (const [kind2, label, glyph] of [["save", "Save", '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 3.75h11A1.75 1.75 0 0 1 19.25 5.5v15L12 16.2l-7.25 4.3v-15A1.75 1.75 0 0 1 6.5 3.75Z"/></svg>'], ["love", "Love", "\u2661\uFE0E"], ["dislike", "Not for me", "\u2193\uFE0E"]]) {
      actions.append(feedAction(label, !!post.feedback?.reactions?.[kind2], async () => {
        try {
          const active = !post.feedback?.reactions?.[kind2];
          const result = await api("/api/feed/react", { id: post.id, kind: kind2, active });
          post.feedback = result.feedback;
          drawActions();
        } catch (error) {
          notice(error.message);
        }
      }, glyph));
    }
    actions.append(feedAction("Reply", false, () => feedDetail(post), "\u21A9\uFE0E"));
  };
  drawActions();
  parent.append(article);
}
async function feed(parent) {
  intro(parent, "Feed", "The world and your work", "Small stories worth knowing, seeing, or hearing.");
  const choices = el("div", "feed-filters");
  parent.append(choices);
  for (const [key, name] of [["all", "For you"], ["latest", "Latest"], ["following", "Following"], ["world", "World"], ["politics", "Politics"], ["ai", "AI"], ["science", "Science"], ["culture", "Culture"], ["history", "History"], ["business", "Business"], ["technology", "Tech"], ["work", "Work"], ["listen", "Listen"], ["saved", "Saved"]]) {
    choices.append(button(name, () => {
      feedCategory = key;
      route();
    }, "feed-filter" + (feedCategory === key ? " active" : "")));
  }
  await guarded(parent, () => dailyDigests(parent));
  const stream = el("div", "feed-stream");
  parent.append(stream);
  await guarded(stream, async () => {
    const data = await api("/api/feed?category=" + encodeURIComponent(feedCategory));
    if (!data.items.length) empty(stream, "No published posts in this category yet.");
    for (const post of data.items) renderFeedPost(stream, post);
    if (data.next_cursor !== null) {
      const more = button("More posts", async () => {
        more.disabled = true;
        try {
          await feedMore(stream, data.next_cursor);
          more.remove();
        } catch (error) {
          more.disabled = false;
          notice(error.message);
        }
      });
      stream.append(more);
    }
  });
  const podcasts = section(parent, "Listen");
  await guarded(podcasts, async () => {
    const data = await api("/api/media?kind=podcast");
    for (const item of data.items.slice(0, 2)) podcasts.append(card(item.title, `${Math.round(item.duration_s / 60)} minutes \xB7 ${item.date}`, () => mediaDetail(item.id)));
  });
}
async function feedMore(parent, cursor) {
  const data = await api(`/api/feed?category=${encodeURIComponent(feedCategory)}&cursor=${cursor}`);
  for (const post of data.items) renderFeedPost(parent, post);
  if (data.next_cursor !== null) {
    const more = button("More posts", async () => {
      more.disabled = true;
      try {
        await feedMore(parent, data.next_cursor);
        more.remove();
      } catch (error) {
        more.disabled = false;
        notice(error.message);
      }
    });
    parent.append(more);
  }
}
async function ask(parent) {
  const chat = el("section", "ask-page");
  parent.append(chat);
  const advanced = el("details", "ask-advanced");
  advanced.append(el("summary", "", "Advanced \xB7 model choice"));
  const picker = el("select", "ask-model");
  picker.setAttribute("aria-label", "Office model");
  advanced.append(picker);
  const toolbar = el("div", "ask-toolbar");
  const selectMessages = button("Select messages", () => {
    if (!current2) return;
    selectionMode = !selectionMode;
    selected.clear();
    draw(current2);
  }, "ask-select");
  const copySelected = button("Copy selected", () => copyRows(current2.messages.filter((row) => selected.has(row.id))), "ask-copy-selected");
  toolbar.append(selectMessages, copySelected, advanced);
  chat.append(toolbar);
  const scrollRegion = el("div", "ask-scroll-region");
  const thread = el("div", "ask-thread");
  const jump = button("\u2193", () => {
    thread.scrollTop = thread.scrollHeight;
    updateJump();
  }, "ask-jump");
  jump.setAttribute("aria-label", "Jump to latest message");
  jump.hidden = true;
  scrollRegion.append(thread, jump);
  chat.append(scrollRegion);
  const form = el("form", "ask-compose");
  const input = el("textarea");
  input.placeholder = "Ask what happened, why work is waiting, or what to fix\u2026";
  input.setAttribute("aria-label", "Ask Office");
  input.rows = 2;
  const imageBox = el("div", "ask-image-box");
  const attached = attachments(imageBox, [], () => {
  }, { imagesOnly: true, compact: true });
  const composeRow = el("div", "ask-compose-row");
  const queueStatus = el("p", "ask-queue-status");
  queueStatus.setAttribute("aria-live", "polite");
  const submit = el("button", "primary", "Send");
  submit.type = "submit";
  composeRow.append(input, submit);
  form.append(imageBox, composeRow);
  chat.append(queueStatus, form);
  let current2 = null, rendered = false, selectionMode = false, draw = () => {
  };
  const selected = /* @__PURE__ */ new Set();
  const activityOpen = /* @__PURE__ */ new Map(), activityStatus = /* @__PURE__ */ new Map();
  const messageText = (row) => row.text || (["queued", "working"].includes(row.status) ? row.status === "queued" ? "Queued behind earlier messages\u2026" : "Working\u2026" : "");
  async function copyRows(rows) {
    if (!rows.length) return;
    const copied = rows.map((row) => {
      const prefix = rows.length === 1 ? "" : `${row.role === "user" ? "You" : "Office"} \xB7 ${new Date(row.created_at * 1e3).toLocaleString()}
`;
      const images = (row.images || []).map((item) => `[Image: ${item.name}]`);
      return prefix + [messageText(row), ...images].filter(Boolean).join("\n");
    }).join("\n\n");
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(copied);
    else {
      const field2 = el("textarea");
      field2.value = copied;
      document.body.append(field2);
      field2.select();
      const ok = document.execCommand("copy");
      field2.remove();
      if (!ok) throw Error("Could not copy messages");
    }
    notice(rows.length === 1 ? "Message copied" : `${rows.length} messages copied`);
  }
  const distanceFromBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight;
  const updateJump = () => {
    jump.hidden = !rendered || distanceFromBottom() < 64;
  };
  thread.addEventListener("scroll", updateJump);
  await guarded(chat, async () => {
    const data = await api("/api/ask");
    const clearDraft = restoreDraft(input, ["ask"]);
    const pendingKey2 = "office-ask-pending";
    let pending2;
    try {
      pending2 = JSON.parse(localStorage.getItem(pendingKey2) || "null");
    } catch {
      localStorage.removeItem(pendingKey2);
    }
    if (!pending2?.request_id || !pending2?.text && !pending2?.images?.length || !pending2?.model) pending2 = null;
    const initial = el("option", "", data.selection || data.model);
    initial.value = data.selection || data.model;
    picker.append(initial);
    draw = (state, toBottom = false) => {
      const follow = toBottom || !rendered || distanceFromBottom() < 64, previousTop = thread.scrollTop;
      current2 = state;
      thread.replaceChildren();
      selectMessages.textContent = selectionMode ? "Cancel selection" : "Select messages";
      copySelected.hidden = !selectionMode;
      copySelected.disabled = !selected.size;
      copySelected.textContent = `Copy ${selected.size} selected`;
      if (!state.messages.length) {
        thread.append(el("p", "ask-intro", "Ask in your own words. Office will bring back the answer and where it came from."));
        for (const prompt of ["Is anything blocked on me?", "What happened while I was asleep?", "Why isn\u2019t HomeClass moving?"])
          thread.append(button(prompt, () => {
            input.value = prompt;
            input.focus();
          }, "ask-prompt"));
      }
      const lastAnswer = [...state.messages].reverse().find((row) => row.role === "office" && row.status === "completed");
      for (const row of state.messages) {
        const bubble = el("article", "ask-bubble " + row.role);
        const label = row.role === "user" ? "You" : row.role === "office" ? "Office \xB7 " + (row.model || "") : row.text;
        const heading = el("small");
        heading.append(el("span", "ask-author", label));
        if (row.role !== "system") heading.append(el("span", "ask-status is-" + row.status, row.status));
        bubble.append(heading);
        if (row.role !== "system") {
          if (selectionMode) {
            const choose = el("input");
            choose.type = "checkbox";
            choose.checked = selected.has(row.id);
            choose.setAttribute("aria-label", `Select ${label} message`);
            choose.addEventListener("change", () => {
              if (choose.checked) selected.add(row.id);
              else selected.delete(row.id);
              copySelected.disabled = !selected.size;
              copySelected.textContent = `Copy ${selected.size} selected`;
            });
            const chooseLabel = el("label", "ask-select-label");
            chooseLabel.append(choose);
            heading.prepend(chooseLabel);
          }
          const waiting = row.status === "queued" ? "Queued behind earlier messages\u2026" : row.status === "working" ? "Working\u2026" : "";
          const copy = markdownView(row.text || waiting, { text: officeLinkText });
          copy.classList.add("ask-copy");
          bubble.addEventListener("click", (event) => {
            const anchor2 = event.target.closest("a");
            if (!anchor2) return;
            const match = anchor2.href.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(issues|pull)\/(\d+)/);
            if (match) {
              event.preventDefault();
              githubDetail(match[1], { number: Number(match[3]) }, match[2] === "pull" ? "prs" : "issues").catch((error) => notice(error.message));
            }
          });
          bubble.append(copy);
          if (row.images?.length) {
            const gallery = el("div", "ask-gallery");
            for (const item of row.images) {
              const url = `/api/uploads/content?id=${encodeURIComponent(item.id)}&revision=${encodeURIComponent(item.revision)}`;
              const imageLink = link(item.name, url);
              imageLink.classList.add("ask-image");
              const preview2 = el("img");
              preview2.src = url;
              preview2.alt = item.name;
              imageLink.replaceChildren(preview2, el("span", "", item.name));
              gallery.append(imageLink);
            }
            bubble.append(gallery);
          }
          if (row.role === "office") {
            const activity = row.activity || [];
            const same = (a, b) => String(a || "").trim().replace(/\s+/g, " ").toLowerCase() === String(b || "").trim().replace(/\s+/g, " ").toLowerCase();
            const currentUpdate = row.status === "working" && activity.length && same(activity.at(-1).text, row.text);
            const history2 = currentUpdate ? activity.slice(0, -1) : activity;
            if (currentUpdate) {
              const stamp = el("time", "ask-activity-time", new Date(activity.at(-1).created_at * 1e3).toLocaleString());
              stamp.dateTime = new Date(activity.at(-1).created_at * 1e3).toISOString();
              heading.append(stamp);
            }
            const previousStatus = activityStatus.get(row.id);
            if (previousStatus === "working" && row.status !== "working") activityOpen.set(row.id, false);
            activityStatus.set(row.id, row.status);
            if (history2.length) {
              const details = el("details", "ask-activity");
              details.open = activityOpen.get(row.id) ?? row.status === "working";
              const count = history2.length;
              details.append(el("summary", "", row.status === "working" ? `${count} earlier ${count === 1 ? "update" : "updates"}` : `${count} ${count === 1 ? "update" : "updates"} \xB7 view activity`));
              details.addEventListener("toggle", () => activityOpen.set(row.id, details.open));
              const list = el("ol", "ask-activity-list");
              for (const update of history2.slice().reverse()) {
                const item = el("li", "ask-activity-item");
                const date = new Date(update.created_at * 1e3);
                const stamp = el("time", "ask-activity-time", date.toLocaleString());
                stamp.dateTime = date.toISOString();
                item.append(stamp, markdownView(update.text, { text: officeLinkText }));
                list.append(item);
              }
              details.append(list);
              bubble.append(details);
            }
          }
          if (row.id === lastAnswer?.id) {
            const rating = el("div", "ask-rating");
            rating.append(el("span", "muted", "Useful?"));
            for (const [kind, label2] of [["helpful", "Yes"], ["missed", "Missed it"]]) rating.append(button(label2, async () => {
              await api("/api/ask/rate", { reply_id: row.id, kind });
              draw(await api("/api/ask"));
            }, "ask-rate" + (row.rating === kind ? " active" : "")));
            bubble.append(rating);
          }
          if (!selectionMode) bubble.append(button("Copy", () => copyRows([row]), "ask-copy-one"));
        }
        thread.append(bubble);
      }
      const queued = state.queue?.queued || 0, working = state.queue?.working || 0;
      queueStatus.textContent = [working ? `${working} working` : "", queued ? `${queued} queued` : ""].filter(Boolean).join(" \xB7 ");
      queueStatus.hidden = !working && !queued;
      thread.scrollTop = follow ? thread.scrollHeight : previousTop;
      rendered = true;
      updateJump();
    };
    draw(data);
    api("/api/ask/models").then((models) => {
      const chosen = picker.value;
      picker.replaceChildren();
      for (const row of models.items) {
        const option = el("option", "", row.name);
        option.value = row.id;
        picker.append(option);
      }
      picker.value = chosen;
    }).catch((error) => notice("Model list: " + error.message));
    const timer = setInterval(async () => {
      if (!chat.isConnected) {
        clearInterval(timer);
        return;
      }
      if (!current2?.busy) return;
      try {
        draw(await api("/api/ask"));
      } catch (error) {
        notice(error.message);
      }
    }, 2500);
    async function sendPending(payload) {
      submit.disabled = true;
      try {
        await api("/api/ask/send", payload);
        if (JSON.parse(localStorage.getItem(pendingKey2) || "null")?.request_id === payload.request_id) {
          localStorage.removeItem(pendingKey2);
          pending2 = null;
        }
        clearDraft(payload.text);
        if (JSON.stringify(attached.references()) === JSON.stringify(payload.images || [])) attached.clear();
        draw(await api("/api/ask"), true);
      } catch (error) {
        if ([400, 403, 404, 409, 413, 422].includes(error.status)) {
          localStorage.removeItem(pendingKey2);
          pending2 = null;
        }
        notice(error.message);
      } finally {
        submit.disabled = false;
      }
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (pending2) {
        void sendPending(pending2);
        return;
      }
      const text = input.value.trim(), images = attached.references();
      if (!text && !images.length || !attached.ready()) return;
      input.value = text;
      input.dispatchEvent(new Event("input"));
      pending2 = { request_id: crypto.randomUUID(), text, images, model: picker.value };
      localStorage.setItem(pendingKey2, JSON.stringify(pending2));
      void sendPending(pending2);
    });
    if (pending2) void sendPending(pending2);
  });
}
function officeLinkText(node, value3) {
  const expression = /https:\/\/[^\s<>]+/g;
  let at = 0;
  for (const match of value3.matchAll(expression)) {
    if (match.index > at) node.append(document.createTextNode(value3.slice(at, match.index)));
    const url = match[0].replace(/[.,;!?]+$/, "");
    node.append(link(url, url));
    at = match.index + url.length;
  }
  if (at < value3.length) node.append(document.createTextNode(value3.slice(at)));
}
async function find(parent) {
  intro(parent, "Find", "Everything has a place", "Search, read, and follow an object back to the work that made it.");
  const projects = section(parent, "Projects");
  await guarded(projects, () => projectRoster(projects));
  const libraryBox = el("details", "find-group");
  libraryBox.append(el("summary", "", "Files, podcasts, and saved items"));
  parent.append(libraryBox);
  libraryBox.addEventListener("toggle", () => {
    if (libraryBox.open && libraryBox.childElementCount === 1) library(libraryBox).catch((error) => failure(libraryBox, error));
  });
  const workBox = el("details", "find-group");
  workBox.append(el("summary", "", "Tasks and conversations"));
  parent.append(workBox);
  workBox.addEventListener("toggle", () => {
    if (workBox.open && workBox.childElementCount === 1) work(workBox).catch((error) => failure(workBox, error));
  });
  const systemBox = el("details", "find-group");
  systemBox.append(el("summary", "", "Schedules, runs, and settings"));
  parent.append(systemBox);
  systemBox.addEventListener("toggle", () => {
    if (systemBox.open && systemBox.childElementCount === 1) system(systemBox).catch((error) => failure(systemBox, error));
  });
}
async function documentView(parent) {
  const params = new URLSearchParams(location.hash.split("?").slice(1).join("?"));
  const repo = params.get("repo"), path = params.get("path");
  if (!repo || !path) {
    failure(parent, Error("No document was requested."));
    return;
  }
  intro(parent, repo, path, "Checkout document");
  const body = section(parent, "Document");
  try {
    const data = await api(`/api/context?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}`);
    if (data.path !== path) throw Error("The Office returned a different document.");
    body.append(el("p", "muted", `${repo} / ${data.path}`), markdownView(data.text));
  } catch (error) {
    failure(body, error);
  }
}
async function route() {
  const [pageRaw, parameters] = location.hash.slice(1).split("?");
  const page = pageRaw || "watch";
  const params = new URLSearchParams(parameters || "");
  if (page === "document") {
    if ($("#detail").open) $("#detail").close();
    const url = new URL(location.href);
    if (url.searchParams.has("detail")) {
      url.searchParams.delete("detail");
      history.replaceState({}, "", url);
    }
  }
  if (page === "coordinator" && ["tbs", "matra"].includes(params.get("id"))) localStorage.setItem("office-coordinator-pick", params.get("id"));
  const views = { watch, feed, ask, find, today, work, coordinator, library, system, document: documentView };
  const parent = $("#content");
  parent.replaceChildren();
  document.body.dataset.page = page;
  for (const item of document.querySelectorAll(".tabs a")) item.setAttribute("aria-current", item.hash === `#${page}` ? "page" : "false");
  const view = el("div");
  parent.append(view);
  await guarded(view, () => (views[page] || watch)(view));
  if (page === "find" && params.get("q")) {
    $("#global-search").value = params.get("q");
    await search();
  }
}
$("#settings").addEventListener("click", settings);
$("#new-task").addEventListener("click", () => newTask().catch((error) => notice(error.message)));
$("#close-detail").addEventListener("click", backDetail);
$("#search-go").addEventListener("click", () => search());
$("#global-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") search();
});
window.addEventListener("hashchange", route);
$("#settings").disabled = false;
$("#new-task").disabled = false;
async function connection() {
  try {
    const data = await api("/api/health");
    $("#connection").textContent = data.ok ? "Mac connected" : "Mac needs attention";
  } catch {
    $("#connection").textContent = "Mac unreachable";
  }
}
await synchronize();
await loadSettings().catch((error) => notice(error.message));
await connection();
await route();
if (new URL(location.href).searchParams.has("detail") && !$("#detail").open) await restoreFromURL();
setInterval(connection, 3e4);
async function archives(parent, cursor = 0) {
  const data = await api(`/api/archives?cursor=${cursor}`);
  for (const row of data.items) parent.append(card(row.title, `${row.profile} \xB7 ${row.engine} \xB7 ${row.cwd}`, () => archiveDetail(row.id)));
  for (const error of data.errors) parent.append(el("p", "error", error.error));
  if (data.next_cursor !== null) parent.append(button("Older conversations", () => archives(parent, data.next_cursor)));
}
function archiveMessages(parent, items) {
  for (const item of items) {
    const node = item.structured ? el("details", "card") : el("article", "card");
    node.append(el(item.structured ? "summary" : "h3", "", item.role), item.structured ? el("pre", "", item.text) : markdownView(item.text));
    parent.append(node);
  }
}
async function archiveDetail(id) {
  rememberDetail("archive", id);
  const data = await api(`/api/archives/messages?id=${encodeURIComponent(id)}&offset=-1`);
  const body = sheet(`${data.session.profile} \xB7 ${data.session.engine}`);
  body.append(el("p", "muted", `${data.session.engine_session_id} \xB7 ${data.session.cwd}`), button("Raw retained records", () => archiveRaw(id)));
  const older = button("Load earlier messages", async () => {
    const prior = await api(`/api/archives/messages?id=${encodeURIComponent(id)}&offset=${older.dataset.offset}`);
    const fragment = el("div");
    archiveMessages(fragment, prior.items);
    const height = body.scrollHeight;
    turns.prepend(...fragment.childNodes);
    body.scrollTop += body.scrollHeight - height;
    older.dataset.offset = prior.previous_offset;
    older.hidden = prior.previous_offset === null;
  });
  older.dataset.offset = data.previous_offset;
  older.hidden = data.previous_offset === null;
  const turns = el("div", "stack");
  body.append(older, turns);
  archiveMessages(turns, data.items);
  older.classList.add("transcript-earlier");
  transcriptNavigation(body, turns).append(older);
}
async function archiveRaw(id, offset = 0, parent = null) {
  const data = await api(`/api/archives/detail?id=${encodeURIComponent(id)}&offset=${offset}`);
  const body = parent || sheet("Raw retained records");
  body.append(el("pre", "", data.text));
  if (data.next_offset !== null) body.append(button("Continue raw records", () => archiveRaw(id, data.next_offset, body)));
}
function openSearchResult(item) {
  if (item.id.startsWith("job-log:")) return jobLog(item.id.slice(8));
  if (item.id.startsWith("flight-log:")) {
    const [id, lane] = JSON.parse(item.id.slice(11));
    return logPage(sheet("Run log"), id, 0, lane);
  }
  if (item.id.startsWith("bot-history:")) return botHistory(item.id);
  if (item.id.startsWith("projection:")) return projectionDetail(item.id);
  if (item.kind === "conversation") return archiveDetail(item.id);
  if (["podcast", "substrate"].includes(item.kind)) return mediaDetail(item.id);
  return openFile(item.id);
}
async function systemCommand(action, id) {
  const key = "office-system-pending:" + JSON.stringify([action, id]);
  const pending2 = JSON.parse(localStorage.getItem(key) || "null") || { action, id, request_id: crypto.randomUUID() };
  localStorage.setItem(key, JSON.stringify(pending2));
  let result;
  try {
    result = await api("/api/system/command", pending2);
  } catch (error) {
    if ([400, 403, 404, 409, 413, 422].includes(error.status)) localStorage.removeItem(key);
    throw error;
  }
  if (result.result?.state !== "unconfirmed") localStorage.removeItem(key);
  notice(JSON.stringify(result.result));
  if (result.result?.state === "unconfirmed") {
    const body = sheet("Check automation delivery");
    body.append(el("p", "", result.result.detail), button("Check current outcome", () => systemCommand(action, id)));
  }
}
async function officeSections(parent, keys) {
  const data = await world();
  for (const key of keys) {
    const value3 = data.sections?.[key];
    if (!value3) continue;
    const group = section(parent, key.replaceAll("_", " "));
    group.append(card(value3.card?.title || key, value3.state, () => {
      const body = sheet(key);
      body.append(structured(value3));
    }));
  }
}
function structured(value3) {
  if (value3 === null || typeof value3 !== "object") return el("p", "", String(value3 ?? "\u2014"));
  const container = el("div");
  for (const [key, item] of Object.entries(value3)) {
    if (key === "card") continue;
    const details = el("details");
    details.append(el("summary", "", key));
    details.append(structured(item));
    container.append(details);
  }
  return container;
}
async function botConversation(bot) {
  rememberDetail("bot", bot.id);
  const body = el("div");
  sheet(bot.name).append(body);
  const history2 = el("div");
  body.append(button("All retained messages", () => botHistory("bot-history:" + bot.id + ".jsonl")), button("Archived office conversations", botArchives), history2);
  let seen = "", refreshing = false;
  const refresh = async () => {
    if (refreshing || !body.isConnected) return;
    refreshing = true;
    try {
      const data = await api(`/api/chat?bot=${encodeURIComponent(bot.id)}`);
      if (!body.isConnected) return;
      const signature = JSON.stringify(data.turns || []);
      if (signature === seen) return;
      seen = signature;
      history2.replaceChildren();
      for (const turn of data.turns || []) {
        const row = card(turn.role, turn.content || turn.text);
        for (const item of turn.attachments || []) if (item.office_id) row.append(link(item.name || "Attached file", `/api/uploads/content?id=${encodeURIComponent(item.office_id)}&revision=${item.revision}`));
        history2.append(row);
      }
    } finally {
      refreshing = false;
    }
  };
  let initialError = "";
  try {
    await refresh();
  } catch (error) {
    initialError = error.message;
  }
  if (!body.isConnected) return;
  transcriptNavigation(body, history2);
  const message = field(body, "Message", el("textarea"));
  const clearMessage = restoreDraft(message, ["bot", bot.id]);
  const key = "office-bot-uploads:" + bot.id;
  const attached = attachments(body, JSON.parse(localStorage.getItem(key) || "[]"), (items) => localStorage.setItem(key, JSON.stringify(items)));
  body.append(button("Send", async () => {
    if (!attached.ready()) throw Error("Wait for the attachment upload to finish.");
    const payload = { bot: bot.id, message: message.value, uploads: attached.references() };
    await api("/api/chat", payload);
    clearMessage(payload.message);
    const stored = JSON.parse(localStorage.getItem(key) || "[]").map(({ id, revision: revision2 }) => ({ id, revision: revision2 }));
    if (attached.ready() && JSON.stringify(stored) === JSON.stringify(payload.uploads) && JSON.stringify(attached.references()) === JSON.stringify(payload.uploads)) attached.clear();
    notice("Agent turn queued");
    await refresh();
  }, "primary"), button("Refresh conversation", refresh));
  const status = el("p", "muted", initialError ? "Connection interrupted; retrying. " + initialError : "");
  body.append(status);
  async function poll() {
    if (!body.isConnected || !$("#detail").open) return;
    try {
      await refresh();
      status.textContent = "";
    } catch (error) {
      status.textContent = "Connection interrupted; retrying. " + error.message;
    }
    if (body.isConnected) setTimeout(poll, 3e3);
  }
  setTimeout(poll, 3e3);
}
async function projectionDetail(id, offset = 0, parent = null, revision2 = "") {
  const data = await api(`/api/search/object?id=${encodeURIComponent(id)}&offset=${offset}&revision=${encodeURIComponent(revision2)}`);
  if (data.target?.kind === "github") return githubDetail(data.target.repo, { number: data.target.number }, "issues");
  const body = parent || sheet(data.title);
  body.append(el("p", "muted", `${data.project} \xB7 ${data.coverage}`), el("pre", "", data.text));
  if (!offset) for (const run of data.related_flights || []) body.append(button(`${run.state} \xB7 ${run.id}`, () => flightDetail(run.id)));
  if (data.next_offset !== null) body.append(button("Continue", () => projectionDetail(id, data.next_offset, body, data.revision || "")));
}
async function jobLog(id, offset = 0, parent = null, version = "") {
  if (!parent) rememberDetail("job-log", id);
  const data = await api(`/api/system/job-log?id=${encodeURIComponent(id)}&offset=${offset}&version=${encodeURIComponent(version)}`);
  const body = parent || sheet(id + " log");
  body.append(el("p", "muted", data.state), el("pre", "", data.text));
  if (data.next_offset !== null) body.append(button(data.state === "rotated" ? "Read current log" : "Continue log", () => jobLog(id, data.next_offset, body, data.version)));
}
async function jobReceipts(id, offset = 0, parent = null) {
  if (!parent) rememberDetail("job-history", id);
  const data = await api(`/api/system/job-history?id=${encodeURIComponent(id)}&offset=${offset}`);
  const body = parent || sheet(id + " receipts");
  for (const item of data.items) body.append(structured(item));
  for (const error of data.errors || []) body.append(el("p", "error", error.error));
  if (data.next_offset !== null) body.append(button("Continue history", () => jobReceipts(id, data.next_offset, body)));
}
function restoreDetail(item) {
  const handlers = {
    file: openFile,
    media: mediaDetail,
    task: taskDetail,
    archive: archiveDetail,
    flight: flightDetail,
    "bot-history": botHistory,
    settings,
    folder: (id) => browse(sheet("Files"), id),
    "job-log": jobLog,
    "job-history": jobReceipts,
    project: restoreProject,
    bot: restoreBot,
    hcom: restoreConversation,
    github: (id) => {
      const ref = JSON.parse(id);
      return githubDetail(ref.repo, { number: ref.number }, ref.kind);
    },
    "github-tree": (id) => {
      const ref = JSON.parse(id);
      return githubTree(ref.repo, ref.path, ref.ref);
    }
  };
  if (!handlers[item.kind]) throw Error("This saved view is unavailable");
  return handlers[item.kind](item.id);
}
async function restoreFromURL() {
  const value3 = new URL(location.href).searchParams.get("detail");
  if (!value3) {
    $("#detail").close();
    return;
  }
  try {
    await restoreDetail(JSON.parse(value3));
  } catch (error) {
    notice(error.message);
  }
}
window.addEventListener("popstate", () => {
  route();
  restoreFromURL();
});
document.addEventListener("office-file-task", (event) => newTask(event.detail.project, event.detail).catch((error) => notice(error.message)));
$("#detail").addEventListener("cancel", clearDetail);
document.addEventListener("office-github-detail", (event) => githubDetail(event.detail.repo, { number: event.detail.number }, "issues").catch((error) => notice(error.message)));
document.addEventListener("office-flight-detail", (event) => flightDetail(event.detail).catch((error) => notice(error.message)));
async function githubCollection(repo, kind, cursor = 1, parent = null) {
  const body = parent || sheet(repo + " " + kind);
  const data = await api(`/api/github/collection?repo=${encodeURIComponent(repo)}&kind=${kind}&cursor=${cursor}`);
  for (const item of data.items) body.append(card(`#${item.number} ${item.title}`, item.state, () => githubDetail(repo, item, kind)));
  if (data.next_cursor) body.append(button("Older items", () => githubCollection(repo, kind, data.next_cursor, body)));
}
async function githubReviews(repo, number, cursor = 1, parent = null, inline2 = false) {
  const body = parent || sheet(repo + " reviews");
  const data = await api(`/api/github/reviews?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}&inline=${inline2}`);
  for (const item of data.items) {
    body.append(commentView(item));
    if (item.diff_hunk) body.append(el("p", "muted", `${item.path} \xB7 ${item.commit_id}`), el("pre", "", item.diff_hunk));
  }
  if (data.next_cursor) body.append(button("More reviews", () => githubReviews(repo, number, data.next_cursor, body, inline2)));
  if (!parent) body.append(button("Inline comments", () => githubReviews(repo, number, 1, body, true)));
}
async function refreshAttention() {
  const results = await Promise.allSettled([api("/api/tasks/permissions"), api("/api/gates"), world(), api("/api/buzz")]);
  const items = [], errors = [], failures = [];
  for (const [index, result] of results.entries()) {
    if (result.status === "rejected") {
      errors.push(result.reason.message);
      continue;
    }
    if (index === 3) errors.push(...result.value.errors || []);
    for (const entry of attentionItems(index, result.value)) {
      (automationFailure(entry) ? failures : items).push(entry);
    }
  }
  attention = { items, errors, failures };
  const needsYou = items.filter(requiresYou).length;
  const badge = $("#detail-attention");
  badge.title = needsYou ? `${needsYou} items need you` : "Nothing needs you right now";
  badge.setAttribute("aria-label", `${needsYou ? `${needsYou} items need you` : "Nothing needs you right now"}${errors.length ? "; a decision source is unavailable" : ""}`);
  const label = $('.tabs a[href="#watch"]');
  label.setAttribute("aria-label", needsYou ? `Watch, ${needsYou} items need you` : "Watch, nothing needs you right now");
  const node = $("#needs-list");
  if (node) drawAttention(node);
}
function drawAttention(parent) {
  const existing = new Map([...parent.querySelectorAll("[data-attention-key]")].map((node) => [node.dataset.attentionKey, node]));
  const nodes = attention.errors.map((error) => el("p", "error", error));
  if (!attention.items.length && !attention.errors.length) nodes.push(el("p", "empty", "Nothing waiting for you."));
  const shown = parent.id === "needs-list" ? attention.items.slice(0, 3) : attention.items;
  for (const entry of shown) {
    const key = `${entry.kind}:${entry.item.id}`;
    const node = entry.kind === "permission" ? existing.get(key) || permissionCard(entry.item) : attentionCard(entry);
    node.dataset.attentionKey = key;
    nodes.push(node);
  }
  if (shown.length < attention.items.length) nodes.push(button(`View all ${attention.items.length} items`, () => attentionList(sheet("Needs you"))));
  reconcileChildren(parent, nodes);
}
function reconcileChildren(parent, nodes) {
  let cursor = parent.firstChild;
  for (const node of nodes) {
    if (node === cursor) cursor = cursor.nextSibling;
    else parent.insertBefore(node, cursor);
  }
  while (cursor) {
    const next = cursor.nextSibling;
    cursor.remove();
    cursor = next;
  }
}
function attentionCard(entry) {
  if (entry.kind === "buzz") return buzzDecisionCard(entry.item);
  if (entry.kind === "gate") {
    const node2 = el("article", "card attention-choice");
    node2.append(el("h3", "", "Permission needed"), el("p", "attention-question", entry.item.question || entry.item.title || "Pending decision"));
    const choices2 = el("div", "attention-options");
    for (const [answer, label] of [["allow", "Allow once"], ["deny", "Deny"]]) choices2.append(button(label, async () => {
      const result = await api("/api/gate", { question_id: entry.item.id, answer });
      if (!result.ok) throw Error(result.message || "Answer was not recorded");
      notice("Answer recorded");
      if ($("#detail").open) $("#detail").close();
      await route();
    }, "attention-option"));
    node2.append(choices2);
    return node2;
  }
  const issue = entry.item, decision = issue.decision;
  const node = el("article", "card attention-choice");
  const askedAt = Date.parse(issue.last_word_at || "");
  const head = el("div", "attention-head");
  head.append(el("p", "attention-source", `${entry.repo.split("/")[1]} #${issue.number}${Number.isFinite(askedAt) ? ` \xB7 asked ${formatAge(Date.now() - askedAt)}` : ""}`));
  node.append(head, el("h3", "", issue.title));
  const context = reportLead(issue.body || "");
  if (context && context !== issue.title) node.append(el("p", "attention-context", context.length > 200 ? context.slice(0, 199) + "\u2026" : context));
  if (issue.decision_context) {
    node.append(el("p", "attention-question", "A product decision is still needed for this issue."), markdownView(issue.decision_context));
    node.append(button("Inspect evidence and answer in issue", () => githubDetail(entry.repo, issue, "issues"), "attention-details"));
    return node;
  }
  node.append(el("p", "attention-question", decision.question));
  const choices = el("div", "attention-options");
  let busy = false;
  async function decide(payload) {
    if (busy) return;
    busy = true;
    for (const control of choices.querySelectorAll("button")) control.disabled = true;
    try {
      const result = await api("/api/decision", { repo: entry.repo, issue: String(issue.number), ...payload });
      if (!result.ok) throw Error(result.result || "Decision was not applied");
      snapshot = null;
      snapshotReadAt = 0;
      notice(payload.kind === "close" ? "Outdated issue closed" : "Choice recorded");
      if ($("#detail").open) $("#detail").close();
      await route();
    } catch (error) {
      busy = false;
      for (const control of choices.querySelectorAll("button")) control.disabled = false;
      throw error;
    }
  }
  for (const option of [...decision.options].sort((a, b) => Number(b.recommended) - Number(a.recommended) || a.n - b.n)) {
    const control = button("", () => decide({ kind: "choose", n: option.n, label: option.label }), "attention-option" + (option.recommended ? " is-recommended" : ""));
    control.append(el("strong", "", option.label), el("span", "", option.consequence || "Record this choice"));
    choices.append(control);
  }
  const actions = el("div", "attention-head-actions");
  actions.append(button("Outdated \xB7 close issue", () => decide({ kind: "close", body: "Closing as outdated at Aria\u2019s direction from Office Watch." }), "attention-close"), button("Put away repo", () => setDeskHidden(entry.repo, true), "attention-hide"));
  head.append(actions);
  node.append(choices, button("Open issue details", () => githubDetail(entry.repo, issue, "issues"), "attention-details"));
  return node;
}
function buzzDecisionCard(row) {
  const node = el("article", "card attention-choice");
  node.append(
    el("p", "attention-source", `${row.author} \xB7 TBS #${row.channel} \xB7 ${formatAge(Date.now() - Date.parse(row.at))}`),
    el("h3", "", "A reply needs you"),
    el("p", "attention-question", row.question)
  );
  node.append(button("Inspect source conversation", () => buzzSourceDetail(row.id), "attention-details"));
  return node;
}
async function attentionList(parent) {
  await refreshAttention();
  drawAttention(parent);
}
setInterval(refreshAttention, 1e4);
function attentionItems(index, value3) {
  if (index === 0) return (value3?.items || []).map((item) => ({ kind: "permission", item }));
  if (index === 1) return (value3?.gates || []).map((item) => ({ kind: "gate", item }));
  if (index === 2) {
    const pinned = new Set(value3?.pins || []);
    return (value3?.stations || []).filter((station) => !station.hidden).flatMap((station) => (station.issues || []).filter((issue) => issue.bot_last === true && (issue.automation_failure || issue.decision?.question && issue.decision?.options?.length)).map((issue) => ({ kind: "issue", repo: station.repo, item: { ...issue, id: `${station.repo}#${issue.number}` } }))).sort((a, b) => Number(pinned.has(b.repo)) - Number(pinned.has(a.repo)) || String(b.item.updatedAt || "").localeCompare(String(a.item.updatedAt || "")));
  }
  if (index === 3) return (value3?.items || []).filter((row) => row.needs_you).map((item) => ({ kind: "buzz", item }));
  return [];
}
function automationFailure(entry) {
  return entry.kind === "issue" && !entry.item.decision_context && (entry.item.automation_failure === "missing_decision" || String(entry.item.decision?.question || "").startsWith("The automated pass could not resolve this and did not say what to decide."));
}
function meaningfulFailureLine(text) {
  const lines = String(text || "").split("\n").map((line) => line.trim()).filter((line) => line && !line.startsWith("#") && !line.startsWith("|") && !line.startsWith("- ") && !/^Read[: `]/i.test(line));
  const found = lines.find((line) => /\b(stopping|stopped|root cause|concrete cause|no code change|implemented|commit|does not exist|not implemented)\b/i.test(line)) || lines.find((line) => line.length > 35) || lines[0] || "";
  return found.replace(/^[*\d.\s]+|[*\s]+$/g, "");
}
function automationFailureCard(entry) {
  const issue = entry.item, askedAt = Date.parse(issue.last_word_at || "");
  const node = el("article", "card attention-choice");
  node.append(el("h3", "", `${entry.repo} #${issue.number} \xB7 ${issue.title}`), el("p", "muted", `Unexplained pass${Number.isFinite(askedAt) ? ` \xB7 ${formatAge(Date.now() - askedAt)}` : ""}`));
  const receipt = el("p", "muted", "Checking earlier work and failure receipt\u2026");
  node.append(receipt);
  const actions = el("div", "actions");
  actions.append(button("Inspect history or add guidance", () => githubDetail(entry.repo, issue, "issues")), link("Open on GitHub", issue.url || `https://github.com/${entry.repo}/issues/${issue.number}`));
  node.append(actions);
  api(`/api/github/detail?repo=${encodeURIComponent(entry.repo)}&number=${issue.number}&kind=issues`).then((data) => {
    if (!node.isConnected) return;
    const comments = (data.comments || []).filter((row) => {
      const body = String(row.body || "");
      return !body.includes("<!-- pipeline-bot -->") && !body.includes("<!-- office-request:") && !body.startsWith("$(cat <<'EOF'");
    });
    const meaningful = comments.at(-1), text = String(meaningful?.body || "");
    const lead = meaningfulFailureLine(text);
    receipt.textContent = lead ? `Last substantive report (${formatAge(Date.now() - Date.parse(meaningful.created_at))}): ${lead.slice(0, 350)}` : "No substantive run receipt found in the available issue comments. Inspect the history before retrying.";
  }).catch((error) => {
    if (node.isConnected) receipt.textContent = `Issue history unavailable: ${error.message}. Inspect on GitHub before retrying.`;
  });
  return node;
}
var layoutObserver = new ResizeObserver(() => {
  document.documentElement.style.setProperty("--tabs-height", `${$(".tabs").getBoundingClientRect().height}px`);
  document.documentElement.style.setProperty("--player-height", `${$("#player").getBoundingClientRect().height}px`);
});
layoutObserver.observe($(".tabs"));
layoutObserver.observe($("#player"));
$("#detail-attention").addEventListener("click", () => attentionList(sheet("Needs you")));
$("#detail-search").addEventListener("click", () => {
  clearDetail();
  $("#global-search").focus();
});
async function restoreProject(id) {
  const reference = id.startsWith("{") ? JSON.parse(id) : { repo: id };
  const [data, local] = await Promise.all([world(), api("/api/projects")]);
  const desk = projectDesks(data, local.items).find((row) => reference.root ? row.root === reference.root : row.repo === reference.repo);
  if (!desk) throw Error("Project unavailable");
  return project(desk);
}
async function botArchives() {
  const data = await api("/api/bots/archives");
  const body = sheet("Retained office conversations");
  for (const item of data.items) body.append(card(item.name, new Date(item.modified * 1e3).toLocaleString(), () => botHistory(item.id)));
}
async function botHistory(id) {
  rememberDetail("bot-history", id);
  const data = await api(`/api/bots/history?id=${encodeURIComponent(id)}&offset=-1`);
  const body = sheet(data.name);
  const turns = el("div", "stack");
  const render = (parent, page) => {
    for (const item of page.items) parent.append(card(item.role || "Record", item.content || JSON.stringify(item)));
    for (const error of page.errors) parent.append(el("p", "error", `${error.error} at byte ${error.offset}`));
  };
  const earlier = button("Load earlier messages", async () => {
    const page = await api(`/api/bots/history?id=${encodeURIComponent(id)}&offset=${earlier.dataset.offset}`);
    const fragment = el("div");
    render(fragment, page);
    const height = body.scrollHeight;
    turns.prepend(...fragment.childNodes);
    body.scrollTop += body.scrollHeight - height;
    earlier.dataset.offset = page.previous_offset;
    earlier.hidden = page.previous_offset === null;
  }, "transcript-earlier");
  earlier.dataset.offset = data.previous_offset;
  earlier.hidden = data.previous_offset === null;
  body.append(turns);
  render(turns, data);
  transcriptNavigation(body, turns).append(earlier);
}
function showSearchCoverage(parent, coverage) {
  const details = el("details", "card");
  details.append(el("summary", "", "What search can see"));
  const groups = /* @__PURE__ */ new Map();
  for (const source of coverage.sources || []) {
    if (!groups.has(source.kind)) groups.set(source.kind, []);
    groups.get(source.kind).push(source);
  }
  for (const [kind, rows] of groups) coverageGroup(details, kind, rows, (source) => `${source.source}: ${source.indexed} indexed \xB7 ${source.coverage} \xB7 ${source.state}${source.total === 0 ? " \xB7 0 retained" : ""}`);
  coverageGroup(details, "GitHub collection", coverage.github || [], (repo) => `${repo.repo} \xB7 ${repo.state} \xB7 ${repo.indexed}/${repo.fetched} records${repo.error ? " \xB7 " + repo.error : ""}`);
  parent.append(details);
}
function coverageGroup(parent, title, rows, describe) {
  const group = el("details"), pane = el("div");
  group.append(el("summary", "", `${title} \xB7 ${rows.length} sources`), pane);
  parent.append(group);
  let loaded = false;
  function page(start) {
    pane.replaceChildren(el("p", "muted", `${start + 1}\u2013${Math.min(start + 40, rows.length)} of ${rows.length}`));
    for (const row of rows.slice(start, start + 40)) pane.append(el("p", "muted", describe(row)));
    for (const [label, next] of [["Previous sources", start - 40], ["More sources", start + 40]]) {
      if (next < 0 || next >= rows.length) continue;
      pane.append(button(label, () => {
        page(next);
        group.scrollIntoView({ block: "start" });
      }));
    }
  }
  group.addEventListener("toggle", () => {
    if (group.open && !loaded) {
      loaded = true;
      if (rows.length) page(0);
      else pane.append(el("p", "muted", "No declared sources."));
    }
  });
}
async function githubAction(payload, onConfirmed = () => {
}) {
  const key = "office-github-pending:" + JSON.stringify([payload.action, payload.repo, payload.number || "new"]);
  const pending2 = JSON.parse(localStorage.getItem(key) || "null") || { ...payload, request_id: crypto.randomUUID() };
  localStorage.setItem(key, JSON.stringify(pending2));
  let receipt;
  try {
    receipt = await api("/api/github/command", pending2);
  } catch (error) {
    if ([400, 403, 404, 409, 413, 422].includes(error.status)) localStorage.removeItem(key);
    throw error;
  }
  if (receipt.state === "rejected") localStorage.removeItem(key);
  if (receipt.state === "unconfirmed") githubRecovery(key, pending2, receipt);
  if (receipt.state !== "confirmed") throw Error(receipt.error || "GitHub has not confirmed this action. Inspect the source before submitting another.");
  localStorage.removeItem(key);
  onConfirmed(pending2);
  notice(`Confirmed by GitHub as ${receipt.acting_identity}`);
  return receipt.result;
}
function createIssue(repo) {
  const body = sheet("New issue \xB7 " + repo);
  const key = "office-issue-draft:" + repo;
  const draft2 = JSON.parse(localStorage.getItem(key) || "{}");
  const title = field(body, "Title", el("input")), description = field(body, "Description", el("textarea"));
  title.value = draft2.title || "";
  description.value = draft2.body || "";
  for (const input of [title, description]) input.addEventListener("input", () => localStorage.setItem(key, JSON.stringify({ title: title.value, body: description.value })));
  body.append(button("Create issue", async () => {
    const result = await githubAction({ action: "create", repo, title: title.value, body: description.value });
    localStorage.removeItem(key);
    await githubDetail(repo, result, "issues");
  }, "primary"));
}
function reviewChange(repo, number, head) {
  const body = sheet("Review change");
  body.append(el("p", "muted", "Reviewed commit " + head));
  const event = field(body, "Review", select([["COMMENT", "Comment"], ["APPROVE", "Approve"], ["REQUEST_CHANGES", "Request changes"]], "COMMENT"));
  const message = field(body, "Review notes", el("textarea"));
  const key = `office-review-draft:${repo}:${number}:${head}`;
  message.value = localStorage.getItem(key) || "";
  message.addEventListener("input", () => localStorage.setItem(key, message.value));
  body.append(button("Submit review", async () => {
    await githubAction({ action: "review", repo, number, head, event: event.value, body: message.value });
    localStorage.removeItem(key);
    await githubDetail(repo, { number }, "prs");
  }, "primary"));
}
function editLabels(repo, number, labels) {
  const body = sheet("Issue labels");
  const input = field(body, "Labels, separated by commas", el("input"));
  input.value = labels.map((label) => typeof label === "string" ? label : label.name).join(", ");
  body.append(button("Save labels", () => githubAction({ action: "labels", repo, number, labels: input.value.split(",").map((label) => label.trim()).filter(Boolean) }), "primary"));
}
async function githubTimeline(repo, number, cursor = 1, parent = null) {
  const data = await api(`/api/github/timeline?repo=${encodeURIComponent(repo)}&number=${number}&cursor=${cursor}`);
  const body = parent || sheet("Issue timeline");
  for (const item of data.items) body.append(card(item.event || "Comment", `${item.actor?.login || item.user?.login || ""} \xB7 ${item.created_at || ""}
${item.body || JSON.stringify(item)}`));
  if (data.next_cursor) body.append(button("Older timeline entries", () => githubTimeline(repo, number, data.next_cursor, body)));
}
async function githubChecks(parent, repo, head, cursor = 1, kind = "checks") {
  const data = await api(`/api/github/checks?repo=${encodeURIComponent(repo)}&head=${head}&cursor=${cursor}&kind=${kind}`);
  for (const check of data.items) {
    const node = card(check.name || check.context, check.conclusion || check.state || check.status);
    const url = check.html_url || check.target_url;
    if (url) node.append(link("Open check", url));
    parent.append(node);
  }
  if (data.next_cursor) parent.append(button("More checks", () => githubChecks(parent, repo, head, data.next_cursor, kind)));
  if (kind === "checks" && cursor === 1) await githubChecks(parent, repo, head, 1, "statuses");
}
async function podcastNotifications(parent, cursor = value("seen", "podcasts") || 0) {
  const data = await api(`/api/system/notifications?cursor=${cursor}`);
  for (const item of data.items) parent.append(card("New episode \xB7 " + item.payload.title, `${Math.round(item.payload.duration_s / 60)} minutes`, () => mediaDetail(item.payload.media_id)));
  if (data.items.length) record("seen", "podcasts", data.items.at(-1).id);
  if (data.next_cursor !== null) parent.append(button("More updates", () => podcastNotifications(parent, data.next_cursor)));
}
function githubRecovery(key, pending2, receipt) {
  const body = sheet("Check GitHub delivery");
  body.append(el("p", "", receipt.error || "The connection ended before GitHub confirmed this action."), el("p", "muted", "This check reads GitHub and never repeats the write."));
  body.append(button(receipt.next_page ? "Check next page" : "Check current outcome", async () => {
    const found = await api("/api/github/reconcile", { request_id: pending2.request_id, page: receipt.next_page || 1 });
    if (found.state !== "confirmed") {
      githubRecovery(key, pending2, found);
      return;
    }
    localStorage.removeItem(key);
    body.replaceChildren(el("p", "", "Outcome confirmed from GitHub."));
    if (found.result?.html_url) body.append(link("Open confirmed result", found.result.html_url));
  }));
}
async function activitySinceVisit(parent) {
  const since = value("seen", "board-visit") || 0;
  const view = el("div", "stack");
  parent.append(view);
  const data = await activityPage(view, since);
  if (data.observed_at && view.isConnected) record("seen", "board-visit", data.observed_at);
  parent.append(button("Browse all activity", () => {
    const body = sheet("All activity");
    return activityPage(body, 0);
  }));
}
async function activityPage(parent, since, cursor = "") {
  const query = new URLSearchParams({ limit: "10", since: String(since), cursor });
  const data = await api("/api/board?" + query);
  if (!["ok", "never"].includes(data.state)) throw Error(data.detail || "Activity unavailable");
  const visible = data.posts.filter((item) => !since || item.unreadable || !value("seen", "board-post:" + JSON.stringify([item.account, item.id])));
  for (const item of visible) {
    parent.append(card(item.text, `${item.account} \xB7 ${item.age}`, () => {
      const body = sheet(item.text);
      body.append(markdownView(item.body));
    }));
    if (parent.isConnected && !item.unreadable) record("seen", "board-post:" + JSON.stringify([item.account, item.id]), data.observed_at || Date.now() / 1e3);
  }
  if (!visible.length) empty(parent, data.next_cursor ? "Continue to check more activity." : since ? "No more new activity since your last visit." : "No activity published yet.");
  if (data.next_cursor) {
    const more = button("More activity", async () => {
      await activityPage(parent, since, data.next_cursor);
      more.remove();
    });
    parent.append(more);
  }
  return data;
}
async function projectTasks(parent, desk, outputs = false, cursor = 0) {
  if (!cursor) parent.replaceChildren();
  const data = await api(`/api/tasks?project=${encodeURIComponent(desk.root)}&cursor=${cursor}`);
  for (const item of data.items) {
    if (outputs && !item.flight) continue;
    parent.append(card(item.title, `${item.specification.engine} \xB7 ${item.specification.profile} \xB7 ${item.phase}`, () => outputs ? flightDetail(item.flight.id) : taskDetail(item.id)));
  }
  if (!data.items.length) empty(parent, outputs ? "No Office task outputs for this checkout yet." : "No Office conversations for this checkout yet.");
  if (data.next_cursor !== null) {
    const more = button("Older work", async () => {
      await projectTasks(parent, desk, outputs, data.next_cursor);
      more.remove();
    });
    parent.append(more);
  }
}
async function projectAgents(parent, desk) {
  parent.replaceChildren();
  const sessionsView = section(parent, "Addressable agents");
  await guarded(sessionsView, async () => {
    const data = await api("/api/sessions");
    if (!["ok", "empty"].includes(data.state)) throw Error(data.detail || data.state);
    const rows = data.sessions.filter((item) => item.directory === desk.checkoutPath);
    for (const item of rows) sessionsView.append(card(item.name, `${item.tool} \xB7 ${item.status}`, () => conversation2(item)));
    if (!rows.length) empty(sessionsView, "No addressable agent in this checkout directory.");
  });
  const observed = section(parent, "Observed processes");
  await guarded(observed, async () => {
    const data = await api("/api/live");
    if (data.state === "unreadable") throw Error(data.detail);
    const rows = data.sessions.filter((item) => item.cwd === desk.checkoutPath);
    for (const item of rows) observed.append(card(`${item.engine} \xB7 PID ${item.pid}`, "Observed in this checkout directory", () => observedProcess(item)));
    if (!rows.length) empty(observed, "No observed process in this checkout directory.");
  });
  parent.append(button("Office tasks in isolated execution folders", () => projectTasks(parent, desk)));
}
function projectPanel(parent, render) {
  const pane = el("div", "stack");
  parent.replaceChildren(pane);
  return guarded(pane, () => render(pane));
}
async function restoreBot(id) {
  const route2 = location.href;
  const data = await api("/api/bots");
  if (location.href !== route2) return;
  const bot = data.bots.find((item) => item.id === id);
  if (!bot) throw Error("This office voice is no longer available");
  return botConversation(bot);
}
async function restoreConversation(id) {
  const route2 = location.href;
  const saved = JSON.parse(id);
  const data = await api("/api/sessions");
  if (location.href !== route2) return;
  const session = data.sessions.find((item) => saved.session_id ? item.session_id === saved.session_id : item.name === saved.name && item.started_at === saved.started_at && item.directory === saved.directory);
  if (!session) throw Error("This session is no longer addressable. Its retained history is available in Work.");
  return conversation2(session);
}
async function githubContext(reference) {
  const route2 = location.href;
  const data = await api("/api/github/context", reference);
  if (location.href !== route2) return;
  const revision2 = data.attachment.revision;
  return newTask(reference.repo, { id: data.attachment.id, revision: revision2, project: reference.repo, path: reference.path || `pull request #${reference.number}` });
}
function githubLineSelection(parent, reference, text) {
  const box = el("details", "card");
  box.append(el("summary", "", "Ask about selected lines"));
  parent.append(box);
  if (!text) {
    box.append(el("p", "muted", "This file has no lines to select."));
    return;
  }
  const source = el("details");
  source.append(el("summary", "", "Numbered source"));
  box.append(source);
  let rendered = false;
  source.addEventListener("toggle", () => {
    if (source.open && !rendered) {
      source.append(numberedSource({ text, line_start: 1 }));
      rendered = true;
    }
  });
  const count = text.split("\n").length - (text.endsWith("\n") ? 1 : 0);
  const fields = [];
  for (const [label, value3] of [["First line", 1], ["Last line", Math.max(1, count)]]) {
    const input = el("input");
    input.type = "number";
    input.min = 1;
    input.max = count;
    input.value = value3;
    fields.push(field(box, label, input));
  }
  box.append(button("Ask an agent about these lines", () => githubContext({ ...reference, start_line: Number(fields[0].value), end_line: Number(fields[1].value) })));
}
function githubDiff(body, repo, number, data) {
  const details = el("details"), pane = el("div");
  details.append(el("summary", "", "Full diff"), pane);
  body.append(details);
  const show = (page) => {
    pane.replaceChildren(el("p", "muted", `${page.diff_offset || 0}\u2013${(page.diff_offset || 0) + (page.diff || "").length} of ${page.diff_total || 0} characters`), el("pre", "", page.diff || "No textual diff"));
    for (const [label, cursor] of [["Previous section", page.diff_previous_cursor], ["Next section", page.diff_next_cursor]]) {
      if (cursor == null) continue;
      const control = button(label, async () => {
        control.disabled = true;
        try {
          const query = new URLSearchParams({ repo, number, head: data.head.sha, base: data.base.sha, cursor });
          const next = await api("/api/github/diff?" + query);
          if (!details.isConnected) return;
          show(next);
          details.scrollIntoView({ block: "start" });
        } finally {
          control.disabled = false;
        }
      });
      pane.append(control);
    }
  };
  show(data);
}
