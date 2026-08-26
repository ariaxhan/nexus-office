/**
 * A small, safe markdown renderer.
 *
 * Small because the page is self-contained by design: no CDN, and no 40KB
 * dependency for what is fundamentally a line scanner and seven regexes. Safe
 * because issue bodies are written by anyone who can open an issue, which makes
 * them untrusted input rendered into a page that holds a session token. Nothing
 * here ever turns source text into raw HTML: every character is escaped first,
 * and only markup this file generates survives.
 */

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

/** Only http(s) becomes a link. javascript:, data: and friends stay plain text. */
function safeHref(url) {
  const trimmed = url.trim();
  return /^https?:\/\//i.test(trimmed) ? trimmed : null;
}

// A character that cannot survive escaping, so it can never appear in the input
// by the time we use it as a marker. Written as a char code rather than a literal
// so this file stays free of control bytes.
const MARK = String.fromCharCode(0);

/** Inline pass. Runs on ALREADY-ESCAPED text, so it can only add our own tags. */
function inline(text) {
  let out = text;

  // Code spans first: nothing inside a backtick pair should be interpreted.
  // The placeholder is MARK-delimited rather than a bare number, because
  // "in 5 minutes" would otherwise read back as span 5 and silently vanish.
  const spans = [];
  out = out.replace(/`([^`\n]+)`/g, (_, code) => {
    spans.push("<code>" + code + "</code>");
    return MARK + (spans.length - 1) + MARK;
  });

  out = out.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (whole, label, url) => {
    const href = safeHref(url);
    if (!href) return whole;
    return '<a href="' + esc(href) + '" target="_blank" rel="noreferrer noopener">' + label + "</a>";
  });

  // Bare urls, but never one we just wrapped in an href.
  out = out.replace(/(^|[\s(])(https?:\/\/[^\s<>()"]+)/g, (_, pre, url) =>
    pre + '<a href="' + esc(url) + '" target="_blank" rel="noreferrer noopener">' + url + "</a>");

  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

  const back = new RegExp(MARK + "(\\d+)" + MARK, "g");
  return out.replace(back, (_, i) => spans[Number(i)]);
}

/**
 * Block pass. Deliberately a line scanner rather than a real parser: issue bodies
 * are mostly headings, bullets, checkboxes, tables and code, and the failure mode
 * of a scanner is one paragraph looking slightly wrong rather than a page that
 * breaks. A real parser would be more correct and much easier to get subtly unsafe.
 */
export function renderMarkdown(source) {
  const lines = String(source || "")
    .replace(/\r\n?/g, "\n")
    // HTML comments are never meaningful markdown and are how machinery leaks
    // into prose: the pipeline's own idempotency marker is one, and it showed up
    // verbatim in the panel until this line existed.
    .replace(/<!--[\s\S]*?-->/g, "")
    .split("\n");
  const out = [];
  let i = 0;
  let para = [];

  const flush = () => {
    if (para.length) {
      out.push("<p>" + inline(esc(para.join(" "))) + "</p>");
      para = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      flush();
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push("<pre><code>" + esc(body.join("\n")) + "</code></pre>");
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      // Everything shifts down two: an issue body's h1 is not the page's h1.
      const level = Math.min(6, heading[1].length + 2);
      out.push("<h" + level + ">" + inline(esc(heading[2])) + "</h" + level + ">");
      i++;
      continue;
    }

    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      flush();
      out.push("<hr />");
      i++;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      flush();
      const body = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      out.push("<blockquote>" + renderMarkdown(body.join("\n")) + "</blockquote>");
      continue;
    }

    const isRow = (s) => /^\s*\|.*\|\s*$/.test(s || "");
    if (isRow(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      flush();
      const cells = (row) =>
        row.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(esc(c.trim())));
      const head = cells(lines[i]);
      i += 2;
      const body = [];
      while (i < lines.length && isRow(lines[i])) body.push(cells(lines[i++]));
      out.push(
        '<div class="md-table-wrap"><table><thead><tr>' +
        head.map((c) => "<th>" + c + "</th>").join("") +
        "</tr></thead><tbody>" +
        body.map((r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>").join("") +
        "</tbody></table></div>"
      );
      continue;
    }

    const BULLET = /^\s*(?:[-*+]|\d+[.)])\s+/;
    if (BULLET.test(line)) {
      flush();
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && BULLET.test(lines[i])) {
        let text = lines[i].replace(BULLET, "");
        i++;
        // An indented continuation line belongs to the item above it.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !BULLET.test(lines[i])) {
          text += " " + lines[i++].trim();
        }
        const task = /^\[([ xX])\]\s+(.*)$/.exec(text);
        if (task) {
          const done = task[1].toLowerCase() === "x";
          items.push(
            '<li class="md-task"><input type="checkbox" disabled' + (done ? " checked" : "") + " />" +
            "<span" + (done ? ' class="md-done"' : "") + ">" + inline(esc(task[2])) + "</span></li>"
          );
        } else {
          items.push("<li>" + inline(esc(text)) + "</li>");
        }
      }
      const tag = ordered ? "ol" : "ul";
      out.push("<" + tag + ">" + items.join("") + "</" + tag + ">");
      continue;
    }

    if (!line.trim()) {
      flush();
      i++;
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flush();
  return out.join("");
}

/** Render into a node. The single place innerHTML is used, on text we generated. */
export function renderInto(node, source) {
  node.innerHTML = renderMarkdown(source);
  return node;
}
