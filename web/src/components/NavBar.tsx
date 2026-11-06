import clsx from "clsx";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

/** The one top bar used on every page. `overlay` = transparent over a hero until the page scrolls. */
export function NavBar({ overlay = false, sub = false }: { overlay?: boolean; sub?: boolean }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  const link = ({ isActive }: { isActive: boolean }) => clsx("nav__link", isActive && "nav__link--active");
  return (
    <header className={clsx("nav", overlay ? (scrolled ? "nav--scrolled" : "") : "nav--solid")}>
      <div className="nav__inner">
        <Logo sub={sub} />
        <nav className="nav__links" aria-label="Primary">
          <NavLink to="/" end className={link}>Overview</NavLink>
          <NavLink to="/chat" className={link}>Chat</NavLink>
          <NavLink to="/observability" className={link}>Observability</NavLink>
        </nav>
        <div className="nav__actions">
          <ThemeToggle />
          <NavLink to="/chat" className="btn btn--primary btn--sm" style={{ textDecoration: "none" }}>
            Open Chat <ArrowRight size={15} />
          </NavLink>
        </div>
      </div>
    </header>
  );
}
