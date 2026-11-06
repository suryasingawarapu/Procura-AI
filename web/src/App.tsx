import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

const Landing = lazy(() => import("./pages/Landing/Landing"));
const Chat = lazy(() => import("./pages/Chat/Chat"));
const Observability = lazy(() => import("./pages/Observability/Observability"));

export function App() {
  return (
    <Suspense fallback={<div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/observability" element={<Observability />} />
        {/* The previous static pages' URLs keep working. */}
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="/chat.html" element={<Navigate to="/chat" replace />} />
        <Route path="/observability.html" element={<Navigate to="/observability" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
