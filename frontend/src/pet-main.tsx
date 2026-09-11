import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Pet from "./Pet";
import "./Pet.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Pet />
  </StrictMode>,
);
