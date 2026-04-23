import { useState } from "react";
import type { ConversationProvenance } from "../../hooks/useConversationProvenance";

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export default function AckButton({
  summary,
  onCopied,
}: {
  summary: ConversationProvenance;
  onCopied?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  if (summary.datasets.length === 0) {
    return null;
  }

  const handleCopy = async () => {
    await copyText(summary.acknowledgementText);
    setCopied(true);
    onCopied?.();
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <button
      type="button"
      className="ack-button"
      onClick={() => {
        void handleCopy();
      }}
      title="Copy data-center acknowledgement text"
    >
      {copied ? "Copied" : "Copy Acknowledgement"}
    </button>
  );
}
