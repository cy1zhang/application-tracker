import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { AuthGate } from "./auth/AuthGate";
import { authConfigError } from "./auth/config";
import { authSession } from "./auth/session";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthGate session={authSession} configurationError={authConfigError}>
      {({ email, onSignOut, onAccountDeleted }) => (
        <App email={email} onSignOut={onSignOut} onAccountDeleted={onAccountDeleted} />
      )}
    </AuthGate>
  </StrictMode>,
);
