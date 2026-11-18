import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AppRoutes, MissingConfig } from "./app/App";
import { Providers } from "./app/Providers";
import { clerkPublishableKey } from "./app/config";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";
import "./styles/features.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      {clerkPublishableKey ? (
        <Providers publishableKey={clerkPublishableKey}>
          <AppRoutes />
        </Providers>
      ) : (
        <MissingConfig />
      )}
    </BrowserRouter>
  </StrictMode>,
);
