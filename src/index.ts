import { DurableObject } from "cloudflare:workers";

interface Env {
  SELF_CONTAINER: DurableObjectNamespace<SelfContainer>;
  API_ID: string;
  API_HASH: string;
  OWNER_ID: string;
  ADMIN_PASSWORD: string;
  SESSION_STRING: string;
  SESSION_NAME?: string;
  AUTO_START_BOT?: string;
}

const INACTIVITY_MS = 5 * 60 * 60 * 1000;
const WARM_EVERY_MS = 60 * 60 * 1000;
const SNAPSHOT_EVERY_MS = 24 * 60 * 60 * 1000;

function securityHeaders(headers = new Headers()): Headers {
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("X-Frame-Options", "DENY");
  headers.set("Referrer-Policy", "no-referrer");
  headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  headers.set("X-Robots-Tag", "noindex, nofollow");
  headers.set("Cache-Control", "no-store");
  return headers;
}

export class SelfContainer extends DurableObject<Env> {
  private startPromise?: Promise<void>;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);

    if (ctx.container?.running) {
      void ctx.blockConcurrencyWhile(() =>
        ctx.container.setInactivityTimeout(INACTIVITY_MS),
      );
    }
  }

  private envVars(): Record<string, string> {
    const vars: Record<string, string> = {
      API_ID: this.env.API_ID || "",
      API_HASH: this.env.API_HASH || "",
      OWNER_ID: this.env.OWNER_ID || "",
      ADMIN_PASSWORD: this.env.ADMIN_PASSWORD || "",
      SESSION_STRING: this.env.SESSION_STRING || "",
      SESSION_NAME: this.env.SESSION_NAME || "my_account",
      AUTO_START_BOT: this.env.AUTO_START_BOT || "true",
      CONTROL_HOST: "127.0.0.1",
      CONTROL_PORT: "8765",
      PORT: "8080",
    };

    return vars;
  }

  private async startAndWait(): Promise<void> {
    if (this.ctx.container.running) {
      await this.ctx.container.setInactivityTimeout(INACTIVITY_MS);
      return;
    }

    const snapshot = await this.ctx.storage.get<{ id: string }>("snapshot");

    if (snapshot?.id) {
      try {
        this.ctx.container.start({
          containerSnapshot: snapshot,
          enableInternet: true,
          instance: "standard-1",
          env: this.envVars(),
        });
      } catch {
        await this.ctx.storage.delete("snapshot");
        this.ctx.container.start({
          image: this.ctx.container.images.base,
          enableInternet: true,
          instance: "standard-1",
          env: this.envVars(),
        });
      }
    } else {
      this.ctx.container.start({
        image: this.ctx.container.images.base,
        enableInternet: true,
        instance: "standard-1",
        env: this.envVars(),
      });
    }

    await this.ctx.container.setInactivityTimeout(INACTIVITY_MS);

    const port = this.ctx.container.getTcpPort(8080);
    let lastError: unknown;

    for (let attempt = 0; attempt < 90; attempt += 1) {
      try {
        const response = await port.fetch(
          "http://container/health",
          { signal: AbortSignal.timeout(1000) },
        );
        await response.body?.cancel();
        if (response.ok) return;
        lastError = new Error(`Container health returned ${response.status}`);
      } catch (error) {
        lastError = error;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }

    throw new Error(
      `Container did not become ready on port 8080: ${String(lastError)}`,
    );
  }

  private async ensureStarted(): Promise<void> {
    this.startPromise ??= this.startAndWait().finally(() => {
      this.startPromise = undefined;
    });
    await this.startPromise;
  }

  async warm(): Promise<void> {
    await this.ensureStarted();

    const response = await this.ctx.container
      .getTcpPort(8080)
      .fetch("http://container/health", {
        signal: AbortSignal.timeout(3000),
      });

    await response.body?.cancel();
    this.ctx.container.setInactivityTimeout(INACTIVITY_MS);
  }

  async saveSnapshot(): Promise<void> {
    if (!this.ctx.container.running) return;

    try {
      const snapshot = await this.ctx.container.snapshotContainer({
        name: "self-runtime",
      });
      await this.ctx.storage.put("snapshot", snapshot);
      await this.ctx.storage.put("snapshotSavedAt", Date.now());
    } catch (error) {
      console.error("snapshot failed", error);
    }
  }

  async restart(): Promise<void> {
    if (this.ctx.container.running) {
      await this.ctx.container.signal(15);
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    await this.ctx.storage.delete("snapshot");
    await this.ensureStarted();
  }

  async fetch(request: Request): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: securityHeaders() });
    }

    try {
      await this.ensureStarted();
      const url = new URL(request.url);
      url.protocol = "http:";
      url.host = "container";

      const forwarded = new Request(url.toString(), request);
      const response = await this.ctx.container
        .getTcpPort(8080)
        .fetch(forwarded);

      const headers = new Headers(response.headers);
      securityHeaders(headers);

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    } catch (error) {
      console.error("container request failed", error);
      return new Response(
        JSON.stringify({ ok: false, error: "Container is temporarily unavailable" }),
        {
          status: 503,
          headers: securityHeaders(
            new Headers({ "Content-Type": "application/json; charset=utf-8" }),
          ),
        },
      );
    }
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/edge-health") {
      return new Response(
        JSON.stringify({ ok: true, edge: "cloudflare", service: "self-cloudflare" }),
        {
          status: 200,
          headers: securityHeaders(
            new Headers({ "Content-Type": "application/json; charset=utf-8" }),
          ),
        },
      );
    }

    return env.SELF_CONTAINER.getByName("main").fetch(request);
  },

  async scheduled(_event: ScheduledController, env: Env): Promise<void> {
    const stub = env.SELF_CONTAINER.getByName("main");
    await stub.warm();

    const now = Date.now();
    const savedAt = await stub.ctx.storage.get<number>("snapshotSavedAt").catch(() => undefined);
    if (!savedAt || now - savedAt >= SNAPSHOT_EVERY_MS) {
      await stub.saveSnapshot();
    }

    await stub.ctx.storage.setAlarm(now + WARM_EVERY_MS);
  },
} satisfies ExportedHandler<Env>;
