// Starts the compute engine and gives the UI a typed way to talk to it.
// In version 1 the engine runs in a web worker in the visitor's browser; the
// same interface could reach an engine on another machine (specification 4.2).

import type { Reply, Request } from "./messages";

export interface ComputeEngine {
  send(request: Request): void;
  onReply(handler: (reply: Reply) => void): void;
  close(): void;
}

export function connect(): ComputeEngine {
  const worker = new Worker(new URL("../compute/worker.ts", import.meta.url), { type: "module" });
  return {
    send: (request) => worker.postMessage(request),
    onReply: (handler) => worker.addEventListener("message", (e: MessageEvent<Reply>) => handler(e.data)),
    close: () => worker.terminate(),
  };
}
