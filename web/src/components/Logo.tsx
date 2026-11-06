import { Link } from "react-router-dom";
import { Layers } from "lucide-react";

export function Logo({ sub = false }: { sub?: boolean }) {
  return (
    <Link to="/" className="logo" aria-label="Procurement Coordinator home">
      <span className="logo__mark">
        <Layers size={17} strokeWidth={2.2} />
      </span>
      <span>
        Procurement Coordinator{sub ? <span className="logo__sub"> · Observability</span> : null}
      </span>
    </Link>
  );
}
