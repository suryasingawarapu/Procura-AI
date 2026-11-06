import { AlertCircle, Check, FileText, Mail, Send } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import type { MailPreview } from "./types";

interface Props {
  mail: MailPreview;
  /** Sends the prepared email; rejects with the server's reason if it is refused. */
  onSend: (mailId: string) => Promise<void>;
}

/** A purchase-order email the person reviews, then sends with one press. Nothing goes out until then. */
export function MailCard({ mail, onSend }: Props) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sent = mail.status !== undefined && mail.status !== "draft";

  const send = async () => {
    setSending(true);
    setError(null);
    try {
      await onSend(mail.mail_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mailcard">
      <div className="mailcard__head">
        <span className="mailcard__title">
          <Mail size={15} /> Purchase order email
        </span>
        {sent ? <Badge tone="success">Sent</Badge> : <Badge tone="warning">Draft &middot; not sent</Badge>}
      </div>

      <dl className="mailcard__meta">
        <div>
          <dt>To</dt>
          <dd>
            {mail.to}
            {mail.test_mode && mail.intended_to && mail.intended_to !== mail.to ? (
              <span className="mailcard__note"> (test mode; the vendor's address is {mail.intended_to})</span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>Subject</dt>
          <dd>{mail.subject}</dd>
        </div>
        {mail.attachment_name ? (
          <div>
            <dt>Attached</dt>
            <dd>
              <span className="mailcard__file">
                <FileText size={14} /> {mail.attachment_name}
              </span>
            </dd>
          </div>
        ) : null}
      </dl>

      <pre className="mailcard__body">{mail.body}</pre>

      {error ? (
        <div className="callout callout--danger">
          <AlertCircle size={15} /> <span>{error}</span>
        </div>
      ) : null}

      <div className="mailcard__foot">
        {sent ? (
          <span className="mailcard__done">
            <Check size={15} /> Sent{mail.sent_at ? ` ${new Date(mail.sent_at).toLocaleString()}` : ""}.
          </span>
        ) : (
          <>
            <span className="mailcard__hint">Review it, then press Send. It is only sent when you do.</span>
            <Button variant="primary" size="sm" loading={sending} disabled={sending} onClick={send}>
              {!sending ? <Send size={14} /> : null} Send email
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
