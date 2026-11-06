import { NavBar } from "@/components/NavBar";
import { Hero } from "./Hero";
import { Agents, Faq, Features, Footer, FooterCta, Lifecycle, Memory, Pipeline, Workflow } from "./Sections";
import "./landing.css";

export default function Landing() {
  return (
    <div className="landing">
      <NavBar overlay />
      <main>
        <Hero />
        <Lifecycle />
        <Workflow />
        <Memory />
        <Pipeline />
        <Agents />
        <Features />
        <Faq />
        <FooterCta />
      </main>
      <Footer />
    </div>
  );
}
