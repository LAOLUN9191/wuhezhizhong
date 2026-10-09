const pending = new Map();
let nextId = 0;
let socket;

function rpc(method, params = {}, timeoutMs = 15000) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP request timed out: ${method}`));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

function waitForEvent(method, timeoutMs = 12000) {
  return new Promise((resolve) => {
    const onMessage = (event) => {
      try {
        if (JSON.parse(event.data).method === method) {
          clearTimeout(timer);
          socket.removeEventListener("message", onMessage);
          resolve(true);
        }
      } catch {}
    };
    const timer = setTimeout(() => {
      socket.removeEventListener("message", onMessage);
      resolve(false);
    }, timeoutMs);
    socket.addEventListener("message", onMessage);
  });
}

async function main() {
  const port = Number(process.argv[2]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("invalid CDP port");
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const request = JSON.parse(input);
  const operation = request.operation;
  const data = request.data || {};
  const targetsResponse = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(5000) });
  if (!targetsResponse.ok) throw new Error(`CDP target list returned HTTP ${targetsResponse.status}`);
  const targets = await targetsResponse.json();
  const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (!page) throw new Error("headless browser has no page target");

  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("failed to connect to headless browser CDP")), { once: true });
  });
  socket.addEventListener("message", (event) => {
    try {
      const message = JSON.parse(event.data);
      const slot = pending.get(message.id);
      if (!slot) return;
      clearTimeout(slot.timer);
      pending.delete(message.id);
      if (message.error) slot.reject(new Error(message.error.message || "CDP request failed"));
      else slot.resolve(message.result || {});
    } catch {}
  });

  await rpc("Page.enable");
  await rpc("Runtime.enable");
  let value;
  if (operation === "navigate") {
    if (typeof data.url !== "string" || !data.url.startsWith("https://")) throw new Error("only HTTPS navigation is allowed");
    const loaded = waitForEvent("Page.loadEventFired");
    const result = await rpc("Page.navigate", { url: data.url });
    if (result.errorText) throw new Error(result.errorText);
    await loaded;
    value = await rpc("Runtime.evaluate", {
      expression: "location.href",
      returnByValue: true,
      awaitPromise: true,
    });
    value = value.result && value.result.value;
  } else if (operation === "evaluate") {
    if (typeof data.expression !== "string" || data.expression.length > 20000) throw new Error("invalid browser expression");
    const result = await rpc("Runtime.evaluate", {
      expression: data.expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: false,
    });
    if (result.exceptionDetails) throw new Error("page evaluation failed");
    value = result.result && result.result.value;
  } else {
    throw new Error("unknown CDP operation");
  }

  process.stdout.write(JSON.stringify({ ok: true, value }));
  socket.close();
}

main().catch((error) => {
  process.stderr.write(String(error && error.message ? error.message : error).slice(0, 1200));
  process.exitCode = 1;
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
});
