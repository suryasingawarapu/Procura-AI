import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface Props {
  /** Text to drop into the box (e.g. an item code picked from the side panel); `n` changes on every insert. */
  insert?: { text: string; n: number } | null;
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function Composer({ busy, insert, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Autosize up to ~6 lines.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [value]);

  useEffect(() => {
    if (!insert) return;
    setValue((v) => (v ? `${v} ${insert.text}` : insert.text));
    ref.current?.focus();
  }, [insert]);

  const submit = () => {
    const text = value.trim();
    if (!text || busy) return;
    setValue("");
    onSend(text);
  };

  return (
    <div className="composer">
      <form
        className="composer__bar"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={ref}
          rows={1}
          value={value}
          autoComplete="off"
          placeholder="Message the Coordinator..."
          aria-label="Message"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter inserts a newline.
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <button type="button" className="composer__btn composer__btn--stop" onClick={onStop} aria-label="Stop generating" title="Stop generating">
            <Square size={13} fill="currentColor" />
          </button>
        ) : (
          <button type="submit" className="composer__btn" disabled={!value.trim()} aria-label="Send" title="Send">
            <ArrowUp size={17} strokeWidth={2.4} />
          </button>
        )}
      </form>
      <div className="composer__hint">
        <span className="kbd">Enter</span> to send &middot; <span className="kbd">Shift</span> + <span className="kbd">Enter</span> for a new line
      </div>
    </div>
  );
}
