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

/**
 * Each shot names the page, the width it is captured at, and who is signed in.
 * The three widths are the three layouts: full sidebar, icon rail, drawer.
 */
const SHOTS = [
  { name: "sign-in", path: "/sign-in", width: 1280, height: 860, as: null },
  { name: "chat", path: "/chat", width: 1440, height: 1000, as: "tech@example.com", ask: "What does error code E-04 mean?" },
  { name: "chat-agent", path: "/chat", width: 1440, height: 980, as: "office@example.com", ask: "What is today's date?", then: "What did we install for Priya Raman?", expandTools: true },
  { name: "chat-declined", path: "/chat", width: 1440, height: 640, as: "sales@example.com", ask: "What does error code E-04 mean?" },
  { name: "customers", path: "/customers", width: 1440, height: 1080, as: "office@example.com", type: "Priya Raman" },
  { name: "inventory", path: "/inventory", width: 1440, height: 900, as: "tech@example.com", type: "1-inch PEX ball valve" },
  { name: "admin-users", path: "/admin/users", width: 1440, height: 780, as: "admin@example.com" },
  // The screen pop, driven the way the page itself drives it: the browser posts
  // a webhook and waits for it to come back down the socket.
  { name: "call-screen-pop", path: "/call", width: 1440, height: 780, as: "office@example.com", ring: 0 },
  { name: "call-ambiguous", path: "/call", width: 1440, height: 620, as: "office@example.com", ring: 1 },
  { name: "call-assist", path: "/call", width: 1440, height: 820, as: "office@example.com", assistMs: 11000 },
  { name: "knowledge", path: "/knowledge", width: 1440, height: 900, as: "admin@example.com" },
  { name: "retrieval-technician", path: "/search", width: 1440, height: 900, as: "tech@example.com", search: "What does error code E-04 mean?" },
  { name: "chat-tablet", path: "/chat", width: 834, height: 900, as: "tech@example.com", ask: "What does error code E-04 mean?" },
  { name: "chat-mobile", path: "/chat", width: 390, height: 780, as: "tech@example.com", ask: "What does error code E-04 mean?" },
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
      mobile: shot.width < 768,
      // Below 768 the layout switches on pointer type as well as width, and a
      // desktop Chrome reports a fine pointer however narrow the window is.
      screenOrientation: { angle: 0, type: "portraitPrimary" },
    });
    await send("Emulation.setTouchEmulationEnabled", { enabled: shot.width < 900 });

    await send("Page.navigate", { url: BASE });
    await sleep(900);

    // Authenticate through the API rather than by scripting the form: the shot
    // is of the page, not of the sign-in animation.
    await send("Runtime.evaluate", {
      awaitPromise: true,
      expression: shot.as
        ? `fetch("/api/auth/login", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ email: ${JSON.stringify(shot.as)}, password: ${JSON.stringify(PASSWORD)} })
          })`
        : `fetch("/api/auth/logout", { method: "POST" })`,
    });

    await send("Page.navigate", { url: BASE + shot.path });
    await sleep(1600);

    if (shot.ask) {
      const questions = [shot.ask, shot.then].filter(Boolean);
      for (const question of questions) {
        await send("Runtime.evaluate", {
          awaitPromise: true,
          expression: `(async () => {
            const field = document.querySelector('textarea[aria-label="Ask a question"]');
            const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
            setter.call(field, ${JSON.stringify(question)});
            field.dispatchEvent(new Event("input", { bubbles: true }));
            field.form.requestSubmit();
            await new Promise((resolve) => setTimeout(resolve, 7000));
          })()`,
        });
        await sleep(600);
      }
      await sleep(600);
    }

    if (shot.expandTools) {
      // The trail is collapsed until asked, which is right on the page and
      // wrong in a screenshot whose point is what came back from the tool.
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `(async () => {
          document
            .querySelectorAll('button[aria-expanded="false"]')
            .forEach((button) => button.click());
          await new Promise((resolve) => setTimeout(resolve, 400));
        })()`,
      });
      await sleep(400);
    }

    if (shot.assistMs) {
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `(async () => {
          const button = [...document.querySelectorAll("button")]
            .find((element) => element.textContent.trim() === "Start a call");
          button?.click();
          await new Promise((resolve) => setTimeout(resolve, ${shot.assistMs}));
          document.querySelector('section[aria-label="Live call assistance"]')
            ?.scrollIntoView({ block: "start" });
          await new Promise((resolve) => setTimeout(resolve, 600));
        })()`,
      });
      await sleep(600);
    }

    if (shot.ring !== undefined) {
      // The socket has to be open before the webhook lands, or the pop is
      // published to nobody. Waiting for the "Watching for calls" state is the
      // page's own signal that it is.
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `(async () => {
          const ready = async () => document.body.innerText.includes("Watching for calls");
          for (let i = 0; i < 40 && !(await ready()); i++) {
            await new Promise((r) => setTimeout(r, 250));
          }
          const demo = await (await fetch("/api/calls/demo")).json();
          const entry = demo.numbers[${shot.ring}];
          await fetch("/api/calls/incoming", {
            method: "POST",
            headers: { "content-type": "application/json", [demo.header]: demo.token },
            body: JSON.stringify({ event: "call.ringing", from: entry.number, to: "(207) 555-0100", callId: "CALL-SHOT" }),
          });
          await new Promise((r) => setTimeout(r, 1500));
        })()`,
      });
      await sleep(600);
    }

    if (shot.type) {
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `(async () => {
          const input = document.querySelector('input[type=search]');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
          setter.call(input, ${JSON.stringify(shot.type)});
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.form.requestSubmit();
          await new Promise((resolve) => setTimeout(resolve, 2500));
        })()`,
      });
      await sleep(600);
    }

    if (shot.search) {
      await send("Runtime.evaluate", {
        awaitPromise: true,
        expression: `(async () => {
          const input = document.querySelector('input[type=search]');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
          setter.call(input, ${JSON.stringify(shot.search)});
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.form.requestSubmit();
          await new Promise((resolve) => setTimeout(resolve, 1800));
        })()`,
      });
      await sleep(600);
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
