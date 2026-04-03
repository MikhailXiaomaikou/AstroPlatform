/**
 * Lightweight inline Markdown renderer — handles bold, italic, code, links,
 * headers, and bullet lists without external dependencies.
 */

import { type ReactNode } from "react";

/** Parse inline markdown: **bold**, *italic*, `code`, [link](url) */
function parseInline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < text.length) {
    // Bold: **text** or __text__
    if ((text[i] === "*" && text[i + 1] === "*") || (text[i] === "_" && text[i + 1] === "_")) {
      const delim = text.substring(i, i + 2);
      const end = text.indexOf(delim, i + 2);
      if (end !== -1) {
        parts.push(<strong key={key++}>{text.substring(i + 2, end)}</strong>);
        i = end + 2;
        continue;
      }
    }

    // Italic: *text* or _text_ (single)
    if ((text[i] === "*" || text[i] === "_") && text[i + 1] !== text[i]) {
      const delim = text[i];
      const end = text.indexOf(delim, i + 1);
      if (end !== -1 && end > i + 1) {
        parts.push(<em key={key++}>{text.substring(i + 1, end)}</em>);
        i = end + 1;
        continue;
      }
    }

    // Inline code: `text`
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end !== -1) {
        parts.push(<code key={key++} className="md-inline-code">{text.substring(i + 1, end)}</code>);
        i = end + 1;
        continue;
      }
    }

    // Link: [text](url)
    if (text[i] === "[") {
      const closeB = text.indexOf("]", i + 1);
      if (closeB !== -1 && text[closeB + 1] === "(") {
        const closeP = text.indexOf(")", closeB + 2);
        if (closeP !== -1) {
          const label = text.substring(i + 1, closeB);
          const href = text.substring(closeB + 2, closeP);
          parts.push(
            <a key={key++} href={href} target="_blank" rel="noopener noreferrer" className="md-link">
              {label}
            </a>
          );
          i = closeP + 1;
          continue;
        }
      }
    }

    // Plain text — consume until next special character
    let next = i + 1;
    while (next < text.length && !"*_`[".includes(text[next])) next++;
    parts.push(text.substring(i, next));
    i = next;
  }

  return parts;
}

export default function MarkdownText({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: ReactNode[] = [];
  let key = 0;
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trimStart();

    // Headers: # ## ###
    if (trimmed.startsWith("### ")) {
      elements.push(<h4 key={key++} className="md-h4">{parseInline(trimmed.slice(4))}</h4>);
      i++;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      elements.push(<h3 key={key++} className="md-h3">{parseInline(trimmed.slice(3))}</h3>);
      i++;
      continue;
    }
    if (trimmed.startsWith("# ")) {
      elements.push(<h2 key={key++} className="md-h2">{parseInline(trimmed.slice(2))}</h2>);
      i++;
      continue;
    }

    // Code block: ```
    if (trimmed.startsWith("```")) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      elements.push(
        <pre key={key++} className="md-code-block"><code>{codeLines.join("\n")}</code></pre>
      );
      continue;
    }

    // Bullet list: - or *
    if (/^[-*]\s/.test(trimmed)) {
      const items: ReactNode[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i].trimStart())) {
        items.push(<li key={items.length}>{parseInline(lines[i].trimStart().slice(2))}</li>);
        i++;
      }
      elements.push(<ul key={key++} className="md-list">{items}</ul>);
      continue;
    }

    // Numbered list: 1. 2. etc
    if (/^\d+\.\s/.test(trimmed)) {
      const items: ReactNode[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i].trimStart())) {
        const text = lines[i].trimStart().replace(/^\d+\.\s/, "");
        items.push(<li key={items.length}>{parseInline(text)}</li>);
        i++;
      }
      elements.push(<ol key={key++} className="md-list">{items}</ol>);
      continue;
    }

    // Horizontal rule: --- or ***
    if (/^[-*]{3,}$/.test(trimmed)) {
      elements.push(<hr key={key++} className="md-hr" />);
      i++;
      continue;
    }

    // Empty line
    if (!trimmed) {
      elements.push(<p key={key++}>{"\u00A0"}</p>);
      i++;
      continue;
    }

    // Regular paragraph
    elements.push(<p key={key++}>{parseInline(line)}</p>);
    i++;
  }

  return <>{elements}</>;
}
