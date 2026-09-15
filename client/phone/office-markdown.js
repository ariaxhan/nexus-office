// Basic markdown to DOM. Every character lands in a text node, never innerHTML, so raw HTML in the
// source shows as text. Links open only http(s) or in-app # targets. `text(node, string)` lets a
// caller decorate plain text (the coordinator links issues, shas and paths); code is never decorated.
const plain = (node, value) => node.append(document.createTextNode(value));
function make(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const LIST_ITEM = /^\s*([-*+]|\d+[.)])\s+(.*)$/;
const cells = row => row.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());

export function markdownView(raw, options = {}) {
  const view = make("div", "markdown");
  blocks(view, String(raw || "").replace(/\r\n/g, "\n").split("\n"), options.text || plain);
  return view;
}

function blocks(view, lines, text) {
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) view.append(inline(make("p"), paragraph.join("\n"), text));
    paragraph = [];
  };
  for (let at = 0; at < lines.length; at++) {
    const line = lines[at];
    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      while (++at < lines.length && !/^\s*```/.test(lines[at])) code.push(lines[at]);
      view.append(make("pre", "", code.join("\n")));
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flush();
      view.append(inline(make("h" + Math.min(heading[1].length + 1, 6)), heading[2], text));
      continue;
    }
    if (/^\s*>/.test(line)) {
      flush();
      const quoted = [];
      for (; at < lines.length && /^\s*>/.test(lines[at]); at++) quoted.push(lines[at].replace(/^\s*>\s?/, ""));
      at--;
      const quote = make("blockquote");
      blocks(quote, quoted, text);
      view.append(quote);
      continue;
    }
    if (LIST_ITEM.test(line)) {
      flush();
      const list = make(/\d/.test(line.match(LIST_ITEM)[1]) ? "ol" : "ul");
      for (; at < lines.length && LIST_ITEM.test(lines[at]); at++) list.append(inline(make("li"), lines[at].match(LIST_ITEM)[2], text));
      at--;
      view.append(list);
      continue;
    }
    if (TABLE_ROW.test(line)) {
      flush();
      const rows = [];
      for (; at < lines.length && TABLE_ROW.test(lines[at]); at++) rows.push(cells(lines[at]));
      at--;
      view.append(table(rows, text));
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
}

function table(rows, text) {
  const node = make("table");
  const divider = rows[1] && rows[1].every(cell => /^:?-+:?$/.test(cell));
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

const TOKEN = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|`([^`]+)`|\*([^*\s][^*]*)\*/g;
function inline(node, raw, text) {
  let from = 0;
  for (const match of raw.matchAll(TOKEN)) {
    if (match.index > from) text(node, raw.slice(from, match.index));
    if (match[1] !== undefined) node.append(anchor(match[1], match[2]));
    else if (match[3] !== undefined) node.append(inline(make("strong"), match[3], text));
    else if (match[4] !== undefined) node.append(make("code", "", match[4]));
    else node.append(inline(make("em"), match[5], text));
    from = match.index + match[0].length;
  }
  if (from < raw.length) text(node, raw.slice(from));
  return node;
}

function anchor(label, href) {
  if (!/^(?:https?:\/\/|#)/i.test(href)) return document.createTextNode(label);
  const link = make("a", "", label);
  link.setAttribute("href", href);
  if (href[0] !== "#") {
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
  }
  return link;
}

// A `text` hook that turns each known token (by exact text) into `render(token)`; longest first.
export function tokenText(tokens, render) {
  const byText = new Map(tokens.map(token => [token.text, token]));
  if (!byText.size) return plain;
  const escaped = [...byText.keys()].sort((a, b) => b.length - a.length).map(key => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(escaped.join("|"), "g");
  return (node, value) => {
    let from = 0;
    for (const match of value.matchAll(pattern)) {
      if (match.index > from) plain(node, value.slice(from, match.index));
      node.append(render(byText.get(match[0])));
      from = match.index + match[0].length;
    }
    if (from < value.length) plain(node, value.slice(from));
  };
}
