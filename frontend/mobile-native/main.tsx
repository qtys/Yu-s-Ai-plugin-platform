import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import StandaloneMobileApp from "./StandaloneMobileApp";
import "../src/Mobile.css";
import "./standalone.css";

createRoot(document.getElementById("root")!).render(<StrictMode><StandaloneMobileApp /></StrictMode>);
