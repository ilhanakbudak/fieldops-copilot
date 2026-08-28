/**
 * Captures the README screenshots from a running instance.
 *
 * Drives a locally installed Chrome over the DevTools Protocol rather than
 * pulling in Playwright: this needs a page load, a click, and a screenshot, and
 * a 300 MB browser download to do that is a poor trade in a repository whose
 * whole pitch is that it clones and runs quickly.
 *
 *   npm run dev            (in another terminal)
 *   npm run screenshots
 */
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";

const BASE = process.env.SHOT_BASE ?? "http://localhost:3000";
const OUT = process.env.SHOT_OUT ?? "docs/assets";
const PORT = 9333;
const CHROME =
  process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

/** Signed out, then signed in as each role whose permissions differ visibly. */
const SHOTS = [
  { name: "sign-in", width: 1100, height: 620, as: null },
  { name: "permissions-technician", width: 1100, height: 560, as: "tech@example.com" },
  { name: "permissions-admin", width: 1100, height: 560, as: "admin@example.com" },
];

const PASSWORD = "demo-password-1234";

const chrome = spawn(
  CHROME,
  [
    `--remote-debugging-port=${PORT}`,
    "--headless=new",
    "--hide-scrollbars",
    "--no-first-run",
    `--user-data-dir=/tmp/fieldops-shots`,
    "about:blank",
  ],
  { stdio: "ignore" },
);

/**
 * The websocket of a *page* target, not the browser's. `Page.captureScreenshot`
 * is meaningless on the browser endpoint, and the failure is a silent undefined
 * result rather than an error.
 */
async function pageEndpoint() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const page = (await response.json()).find((target) => target.type === "page");
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch {
      // Chrome is not listening yet.
    }
    await sleep(200);
  }
  throw new Error("Chrome did not start, or exposed no page target");
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const socket = new WebSocket(await pageEndpoint());
  await new Promise((resolve) => socket.addEventListener("open", resolve));

  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    pending.get(message.id)?.(message.result);
    pending.delete(message.id);
  });
  const send = (method, params = {}) =>
    new Promise((resolve) => {
      const messageId = (id += 1);
      pending.set(messageId, resolve);
      socket.send(JSON.stringify({ id: messageId, method, params }));
    });

  await send("Page.enable");

  for (const shot of SHOTS) {
    await send("Emulation.setDeviceMetricsOverride", {
      width: shot.width,
      height: shot.height,
      deviceScaleFactor: 2,
      mobile: false,
    });
    await send("Page.navigate", { url: BASE });
    await sleep(1400);

    if (shot.as) {
      // Sign in through the API so the screenshot shows the signed-in panel
      // without scripting the form.
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `fetch("/api/auth/login", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ email: ${JSON.stringify(shot.as)}, password: ${JSON.stringify(PASSWORD)} })
        })`,
      });
      await send("Page.navigate", { url: BASE });
      await sleep(1400);
    } else {
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `fetch("/api/auth/logout", { method: "POST" })`,
      });
      await send("Page.navigate", { url: BASE });
      await sleep(1200);
    }

    // The dev-mode overlay is not part of the product.
    await send("Runtime.evaluate", {
      expression: `document.querySelectorAll("nextjs-portal").forEach((node) => node.remove())`,
    });

    const { data } = await send("Page.captureScreenshot", { format: "png" });
    await writeFile(`${OUT}/${shot.name}.png`, Buffer.from(data, "base64"));
    console.log(`wrote ${OUT}/${shot.name}.png`);
  }

  socket.close();
  chrome.kill();
}

main().catch((error) => {
  console.error(error);
  chrome.kill();
  process.exit(1);
});
