import type { IncomingMessage, ServerResponse } from "node:http";
import { expect, test, vi } from "vitest";

import { rejectRemoteApiRequest } from "./localOnlyApi";

function fakeRequest(remoteAddress: string | undefined) {
  return { socket: { remoteAddress } } as unknown as IncomingMessage;
}

function fakeResponse() {
  const response = { statusCode: 200, setHeader: vi.fn(), end: vi.fn() };
  return response as typeof response & ServerResponse;
}

test.each(["127.0.0.1", "::1", "::ffff:127.0.0.1"])(
  "passes requests from %s on to the proxy",
  (address) => {
    const next = vi.fn();
    const response = fakeResponse();

    rejectRemoteApiRequest(fakeRequest(address), response, next);

    expect(next).toHaveBeenCalledOnce();
    expect(response.end).not.toHaveBeenCalled();
  },
);

test.each(["192.168.1.20", undefined])(
  "refuses requests from %s",
  (address) => {
    const next = vi.fn();
    const response = fakeResponse();

    rejectRemoteApiRequest(fakeRequest(address), response, next);

    expect(next).not.toHaveBeenCalled();
    expect(response.statusCode).toBe(403);
    expect(JSON.parse(response.end.mock.calls[0][0])).toEqual({
      detail: "The development API only accepts requests from this machine.",
    });
  },
);
