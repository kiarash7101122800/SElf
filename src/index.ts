import { DurableObject } from "cloudflare:workers";

interface Env {
  SELF_CONTAINER: DurableObjectNamespace<SelfContainer>;
  BOT_TOKEN: string;
  TELEGRAM_API_ID: string;
  TELEGRAM_API_HASH: string;
  OWNER_ID: string;
  ADMIN_PASSWORD: string;
  AUTO_START_BOT?: string;
  SESSION_ENCRYPTION_KEY?: string;
  BOT_TIMEZONE?: string;
  BOT_DATA_DIR?: string;
  BOT_SESSIONS_DIR?: string;
  SELF_START_TIMEOUT?: string;
  SELF_WATCHDOG_INTERVAL?: string;
  SELF_RESTART_MAX_BACKOFF?: string;
  SELF_RESTART_CONCURRENCY?: string;
  HELPER_START_TIMEOUT?: string;
  HELPER_WATCHDOG_INTERVAL?: string;
  BETTING_GAME_TTL_MINUTES?: string;
  BETTING_CLEANUP_INTERVAL?: string;
  BETTING_MAX_STAKE?: string;
  BETTING_MAX_OPEN_GAMES_PER_USER?: string;
  BETTING_RATE_WINDOW_SECONDS?: string;
  BETTING_CREATE_RATE_LIMIT?: string;
  BETTING_CANCEL_RATE_LIMIT?: string;
  BETTING_JOIN_RATE_LIMIT?: string;
  BETTING_CLEANUP_BATCH_SIZE?: string;
  BETTING_HISTORY_RETENTION_DAYS?: string;
  BETTING_TRANSACTION_RETENTION_DAYS?: string;
  BETTING_CLOSURE_MAX_RETRIES?: string;
  BETTING_ALLOWED_CHAT_IDS?: string;
  MAX_IN_MEMORY_MEDIA_MB?: string;
}
const INACTIVITY_MS = 6 * 60 * 60 * 1000;
const ALARM_EVERY_MS = 60 * 60 * 1000;
const SNAPSHOT_EVERY_MS = 4 * 60 * 60 * 1000;
const CONFIG_KEYS: (keyof Env)[] = [
  "BOT_TOKEN","TELEGRAM_API_ID","TELEGRAM_API_HASH","OWNER_ID","ADMIN_PASSWORD",
  "AUTO_START_BOT","SESSION_ENCRYPTION_KEY","BOT_TIMEZONE","BOT_DATA_DIR","BOT_SESSIONS_DIR",
  "SELF_START_TIMEOUT","SELF_WATCHDOG_INTERVAL","SELF_RESTART_MAX_BACKOFF","SELF_RESTART_CONCURRENCY",
  "HELPER_START_TIMEOUT","HELPER_WATCHDOG_INTERVAL","BETTING_GAME_TTL_MINUTES","BETTING_CLEANUP_INTERVAL",
  "BETTING_MAX_STAKE","BETTING_MAX_OPEN_GAMES_PER_USER","BETTING_RATE_WINDOW_SECONDS","BETTING_CREATE_RATE_LIMIT",
  "BETTING_CANCEL_RATE_LIMIT","BETTING_JOIN_RATE_LIMIT","BETTING_CLEANUP_BATCH_SIZE","BETTING_HISTORY_RETENTION_DAYS",
  "BETTING_TRANSACTION_RETENTION_DAYS","BETTING_CLOSURE_MAX_RETRIES","BETTING_ALLOWED_CHAT_IDS","MAX_IN_MEMORY_MEDIA_MB"
];
function headers(base=new Headers()): Headers {
  base.set("X-Content-Type-Options","nosniff"); base.set("X-Frame-Options","DENY");
  base.set("Referrer-Policy","no-referrer"); base.set("Permissions-Policy","camera=(), microphone=(), geolocation=()");
  base.set("X-Robots-Tag","noindex, nofollow"); base.set("Cache-Control","no-store"); return base;
}
export class SelfContainer extends DurableObject<Env> {
  private startPromise?: Promise<void>;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx,env);
    if (ctx.container?.running) void ctx.blockConcurrencyWhile(async()=>{await ctx.container!.setInactivityTimeout(INACTIVITY_MS);});
  }
  private envVars(): Record<string,string> {
    const out: Record<string,string>={};
    for(const key of CONFIG_KEYS){const value=this.env[key]; if(typeof value==="string"&&value.length) out[key]=value;}
    out.PORT="8080"; return out;
  }
  private async startAndWait(): Promise<void> {
    const container=this.ctx.container; if(!container) throw new Error("Cloudflare Container binding is unavailable");
    if(container.running){await container.setInactivityTimeout(INACTIVITY_MS); return;}
    const snapshot=await this.ctx.storage.get<ContainerSnapshot>("snapshot");
    const options={enableInternet:true,instance:"standard-2" as const,env:this.envVars()};
    if(snapshot?.id){try{container.start({containerSnapshot:snapshot,...options});}catch{await this.ctx.storage.delete("snapshot");container.start({image:container.images.base,...options});}}
    else container.start({image:container.images.base,...options});
    await container.setInactivityTimeout(INACTIVITY_MS);
    const port=container.getTcpPort(8080); let lastError: unknown;
    for(let attempt=0;attempt<120;attempt++){
      try{
        const response=await port.fetch("http://container/health",{signal:AbortSignal.timeout(1000)});
        await response.body?.cancel();
        if(response.ok){await this.ctx.storage.setAlarm(Date.now()+ALARM_EVERY_MS);return;}
        lastError=new Error(`health=${response.status}`);
      }catch(error){lastError=error;}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    throw new Error(`Container did not become ready on port 8080: ${String(lastError)}`);
  }
  private async ensureStarted(): Promise<void> {
    this.startPromise??=this.startAndWait().finally(()=>{this.startPromise=undefined;}); await this.startPromise;
  }
  private async warm(): Promise<void> {
    await this.ensureStarted(); const container=this.ctx.container; if(!container) throw new Error("Cloudflare Container binding is unavailable");
    const response=await container.getTcpPort(8080).fetch("http://container/health",{signal:AbortSignal.timeout(3000)});
    await response.body?.cancel(); await container.setInactivityTimeout(INACTIVITY_MS); await this.ctx.storage.setAlarm(Date.now()+ALARM_EVERY_MS);
  }
  private async saveSnapshot(): Promise<void> {
    const container=this.ctx.container; if(!container?.running) return;
    try{const snapshot=await container.snapshotContainer({name:"self-runtime"}); await this.ctx.storage.put("snapshot",snapshot); await this.ctx.storage.put("snapshotSavedAt",Date.now());}
    catch(error){console.error("snapshot failed",error);}
  }
  async maintain(): Promise<void> {
    await this.warm(); const savedAt=await this.ctx.storage.get<number>("snapshotSavedAt");
    if(!savedAt||Date.now()-savedAt>=SNAPSHOT_EVERY_MS) await this.saveSnapshot();
    await this.ctx.storage.setAlarm(Date.now()+ALARM_EVERY_MS);
  }
  async alarm(): Promise<void> {
    try{await this.maintain();}catch(error){console.error("maintenance alarm failed",error);await this.ctx.storage.setAlarm(Date.now()+ALARM_EVERY_MS);}
  }
  async restart(): Promise<void> {
    const container=this.ctx.container; if(!container) throw new Error("Cloudflare Container binding is unavailable");
    if(container.running){await container.signal(15);await new Promise(resolve=>setTimeout(resolve,2000));}
    await this.ctx.storage.delete("snapshot"); await this.ensureStarted();
  }
  async fetch(request: Request): Promise<Response> {
    if(request.method==="OPTIONS") return new Response(null,{status:204,headers:headers()});
    try{
      await this.ensureStarted(); const container=this.ctx.container; if(!container) throw new Error("Cloudflare Container binding is unavailable");
      const url=new URL(request.url); url.protocol="http:"; url.host="container";
      const forwarded=new Request(url.toString(),request); forwarded.headers.delete("host");
      const response=await container.getTcpPort(8080).fetch(forwarded);
      return new Response(response.body,{status:response.status,statusText:response.statusText,headers:headers(new Headers(response.headers))});
    }catch(error){
      console.error("container request failed",error);
      return new Response(JSON.stringify({ok:false,error:"Container is temporarily unavailable"}),{status:503,headers:headers(new Headers({"Content-Type":"application/json; charset=utf-8"}))});
    }
  }
}
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url=new URL(request.url);
    if(url.pathname==="/edge-health") return new Response(JSON.stringify({ok:true,edge:"cloudflare",service:"self-cloudflare"}),{status:200,headers:headers(new Headers({"Content-Type":"application/json; charset=utf-8"}))});
    return env.SELF_CONTAINER.getByName("main").fetch(request);
  }
} satisfies ExportedHandler<Env>;
