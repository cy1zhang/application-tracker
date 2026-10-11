import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";

const LOOPBACK_ADDRESSES = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

// The backend trusts loopback clients as LOCAL_DEV_SUBJECT, and the /api proxy
// always connects from loopback, so refuse /api requests that did not start on
// this machine (for example when Vite runs with --host).
export function rejectRemoteApiRequest(
  request: IncomingMessage,
  response: ServerResponse,
  next: () => void,
): void {
  if (LOOPBACK_ADDRESSES.has(request.socket.remoteAddress ?? "")) {
    next();
    return;
  }
  response.statusCode = 403;
  response.setHeader("Content-Type", "application/json");
  response.end(
    JSON.stringify({
      detail: "The development API only accepts requests from this machine.",
    }),
  );
}

export function localOnlyApi(): Plugin {
  return {
    name: "local-only-api",
    // Middlewares added here run before Vite's internal ones, including the proxy.
    configureServer(server) {
      server.middlewares.use("/api", rejectRemoteApiRequest);
    },
  };
}
