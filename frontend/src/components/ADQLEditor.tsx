import { useRef, useCallback, useMemo } from "react";

/* ── Syntax highlighting ── */

function highlightADQL(code: string): string {
  // Escape HTML entities first
  let html = code
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  // Comments (-- to end of line)
  html = html.replace(/(--.*?)$/gm, '<span class="adql-hl-comment">$1</span>');

  // Strings (single-quoted)
  html = html.replace(
    /('(?:[^'\\]|\\.)*')/g,
    '<span class="adql-hl-string">$1</span>',
  );

  // Numbers
  html = html.replace(
    /\b(\d+\.?\d*(?:[eE][+-]?\d+)?)\b/g,
    '<span class="adql-hl-number">$1</span>',
  );

  // ADQL Keywords
  const keywords =
    "SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|ON|AS|AND|OR|NOT|IN|BETWEEN|LIKE|IS|NULL|ORDER|BY|GROUP|HAVING|LIMIT|TOP|DISTINCT|ALL|UNION|INTERSECT|EXCEPT|EXISTS|CASE|WHEN|THEN|ELSE|END|ASC|DESC|OFFSET|SET";
  html = html.replace(
    new RegExp(`\\b(${keywords})\\b`, "gi"),
    '<span class="adql-hl-keyword">$1</span>',
  );

  // ADQL Functions (only when followed by a parenthesis)
  const functions =
    "CONTAINS|POINT|CIRCLE|BOX|POLYGON|REGION|CENTROID|AREA|DISTANCE|INTERSECTS|COORD1|COORD2|COORDSYS|COUNT|SUM|AVG|MIN|MAX|ABS|CEILING|FLOOR|ROUND|TRUNCATE|SQRT|LOG|LOG10|POWER|MOD|PI|RAND|DEGREES|RADIANS|ACOS|ASIN|ATAN|ATAN2|COS|SIN|TAN";
  html = html.replace(
    new RegExp(`\\b(${functions})\\s*(?=\\()`, "gi"),
    '<span class="adql-hl-function">$1</span>',
  );

  return html;
}

/* ── Component ── */

export default function ADQLEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const preRef = useRef<HTMLPreElement>(null);

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLTextAreaElement>) => {
      const ta = e.currentTarget;
      if (preRef.current) {
        preRef.current.scrollTop = ta.scrollTop;
        preRef.current.scrollLeft = ta.scrollLeft;
      }
    },
    [],
  );

  // Append a trailing newline so the pre element always has space for the
  // last line cursor (browsers collapse trailing whitespace in pre).
  const highlighted = useMemo(() => highlightADQL(value) + "\n", [value]);

  return (
    <div className="adql-syn-editor">
      <textarea
        className="adql-syn-textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={handleScroll}
        spellCheck={false}
        placeholder={placeholder}
        rows={6}
      />
      <pre
        ref={preRef}
        className="adql-syn-pre"
        aria-hidden="true"
        dangerouslySetInnerHTML={{ __html: highlighted }}
      />
    </div>
  );
}
