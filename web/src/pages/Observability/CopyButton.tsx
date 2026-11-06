import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/Button";

export function CopyButton({ text, label = "Copy", size = "sm" }: { text: string; label?: string; size?: "sm" | "md" }) {
  const [done, setDone] = useState(false);
  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch {
      /* clipboard unavailable: nothing else to fall back to */
    }
  };
  return (
    <Button variant="ghost" size={size} onClick={copy} aria-label={done ? "Copied" : label} title={label}>
      {done ? <Check size={14} /> : <Copy size={14} />}
      {done ? "Copied" : label}
    </Button>
  );
}

export function CopyIcon({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="obs-copyicon"
      aria-label="Copy"
      title="Copy"
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        } catch {
          /* ignore */
        }
      }}
    >
      {done ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
}
