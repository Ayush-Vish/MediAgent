import {
  ChatAnswer,
  ChatAnswerSchema,
  ConfigResponse,
  ConfigResponseSchema,
  PatientSummaryResponse,
  PatientSummaryResponseSchema,
  UserProfile,
  UserProfileSchema,
  parseSessionHistory,
} from "@/types/api";
import { z } from "zod";
import { readSSE } from "@/lib/sse";
import { anonymousHistory, clearAnonymousHistory, migrateBrowserHistory, saveAnonymousTurn } from "@/lib/browser-history";

let sessionUserId: string | null = null;

async function retainAnonymousAnswer(message: string, requestId: string, answer: ChatAnswer, owner: string | null) {
  if (owner === null && sessionUserId === null) {
    await saveAnonymousTurn({ request_id: requestId, question: message, answer, created: Date.now() / 1000 });
  }
}

const BASE_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body && typeof options.body === "string") {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await response.json() : null;

  if (!response.ok) {
    const errorMsg = typeof data?.error === "string" ? data.error
      : typeof data?.detail === "string" ? data.detail : `Request failed (${response.status}). Please retry.`;
    throw new Error(errorMsg);
  }

  return data as T;
}

// ----------------- Auth API -----------------
export const authApi = {
  async me(): Promise<UserProfile | null> {
    try {
      const data = await request<{ user: unknown }>("/api/auth/me");
      const parsed = UserProfileSchema.safeParse(data.user);
      return parsed.success ? parsed.data : null;
    } catch {
      return null;
    }
  },

  async login(email: string, password: string): Promise<UserProfile> {
    const data = await request<{ user: unknown }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    const parsed = UserProfileSchema.safeParse(data.user);
    if (!parsed.success) {
      throw new Error("Invalid patient profile returned from server.");
    }
    return parsed.data;
  },

  async register(patientData: {
    email: string;
    password: string;
    name: string;
    phone?: string;
    age?: number | null;
    gender?: string;
    emergency_contact?: string;
    emergency_phone?: string;
  }): Promise<UserProfile> {
    const data = await request<{ user: unknown }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(patientData),
    });
    const parsed = UserProfileSchema.safeParse(data.user);
    if (!parsed.success) {
      throw new Error("Invalid patient profile returned from server.");
    }
    return parsed.data;
  },

  async logout(): Promise<void> {
    await request("/api/auth/logout", { method: "POST" });
  },
};

// ----------------- Chat & Session API -----------------
export const chatApi = {
  async streamMessage(
    message: string,
    requestId: string,
    onDraft: (text: string) => void,
    onStatus: (text: string) => void,
    signal?: AbortSignal
  ): Promise<ChatAnswer> {
    const owner = sessionUserId;
    const response = await fetch(`${BASE_URL}/api/chat/stream`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      credentials: "include", body: JSON.stringify({ message, request_id: requestId }), signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.error === "string" ? data.error : `Request failed (${response.status}).`);
    }
    if (!response.body) throw new Error("Response streaming is unavailable.");
    let answer: ChatAnswer | undefined;
    await readSSE(response.body, ({ event, data }) => {
      if (event === "draft") onDraft(z.object({ text: z.string() }).parse(data).text);
      if (event === "status") onStatus(z.object({
        stage: z.enum(["starting", "knowledge", "qdrant", "web", "answer"]),
        text: z.string().min(1).max(120),
      }).parse(data).text);
      if (event === "done") answer = ChatAnswerSchema.parse(data);
      if (event === "error") throw new Error(z.object({ error: z.string() }).parse(data).error);
    });
    if (!answer) throw new Error("The response was interrupted. Please retry.");
    await retainAnonymousAnswer(message, requestId, answer, owner);
    return answer;
  },
  async getConfig(): Promise<ConfigResponse> {
    const data = await request<unknown>("/api/config");
    const parsed = ConfigResponseSchema.safeParse(data);
    if (!parsed.success) {
      return { hospital: "Hospital Assistant", portal_url: "", auth_configured: true };
    }
    return parsed.data;
  },

  async initSession() {
    const data = await request<unknown>("/api/session", {
      method: "POST",
    });
    let session = parseSessionHistory(data);
    sessionUserId = session.user_id;
    if (session.user_id) {
      const owner = session.user_id;
      await migrateBrowserHistory(owner, async turns => {
        session = parseSessionHistory(await request("/api/history/import", {
          method: "POST", body: JSON.stringify({ user_id: owner, turns: turns.map(t => ({
            request_id: t.request_id, question: t.question, answer_text: t.answer.text, created: t.created,
          })) }),
        }));
      });
      // A second tab may have completed the import while this tab waited for
      // the browser lock. Reload the authoritative account history either way.
      session = parseSessionHistory(await request("/api/history", { cache: "no-store" }));
      if (session.user_id !== owner) throw new Error("Account changed. Refresh to load your conversation.");
    } else {
      const saved = anonymousHistory();
      const ids = new Set(saved.map(t => t.request_id));
      session = parseSessionHistory({ ...session, turns: [
        ...session.turns.filter(t => !t.request_id || !ids.has(t.request_id)), ...saved,
      ].sort((a, b) => (a.created ?? 0) - (b.created ?? 0)) });
    }
    return session;
  },

  async history() {
    return parseSessionHistory(await request<unknown>("/api/history", { cache: "no-store" }));
  },

  async sendMessage(message: string, requestId?: string): Promise<ChatAnswer> {
    const owner = sessionUserId;
    const payload = {
      message,
      request_id: requestId || crypto.randomUUID(),
    };
    const data = await request<unknown>("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const parsed = ChatAnswerSchema.safeParse(data);
    if (!parsed.success) {
      throw new Error("Received malformed answer format from clinical assistant.");
    }
    await retainAnonymousAnswer(message, payload.request_id, parsed.data, owner);
    return parsed.data;
  },

  async resetSession(): Promise<void> {
    await request("/api/session", { method: "DELETE" });
    if (sessionUserId === null) await clearAnonymousHistory();
    await request("/api/session", { method: "POST" });
  },
};

// ----------------- Doctor Symptom Progression API -----------------
export const symptomApi = {
  async getSummary(sinceDays: number = 30): Promise<PatientSummaryResponse> {
    const data = await request<unknown>(`/api/user/symptom-history?since_days=${sinceDays}`);
    const parsed = PatientSummaryResponseSchema.safeParse(data);
    if (!parsed.success) {
      throw new Error("Failed to parse patient symptom history records.");
    }
    return parsed.data;
  },
};
