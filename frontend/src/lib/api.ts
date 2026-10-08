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

const BASE_URL = "";

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
    credentials: "same-origin",
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
  async streamMessage(message: string, requestId: string, onDraft: (text: string) => void, signal?: AbortSignal): Promise<ChatAnswer> {
    const response = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      credentials: "same-origin", body: JSON.stringify({ message, request_id: requestId }), signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.error === "string" ? data.error : `Request failed (${response.status}).`);
    }
    if (!response.body) throw new Error("Response streaming is unavailable.");
    let answer: ChatAnswer | undefined;
    await readSSE(response.body, ({ event, data }) => {
      if (event === "draft") onDraft(z.object({ text: z.string() }).parse(data).text);
      if (event === "done") answer = ChatAnswerSchema.parse(data);
      if (event === "error") throw new Error(z.object({ error: z.string() }).parse(data).error);
    });
    if (!answer) throw new Error("The response was interrupted. Please retry.");
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
    return parseSessionHistory(data);
  },

  async history() {
    return parseSessionHistory(await request<unknown>("/api/history", { cache: "no-store" }));
  },

  async sendMessage(message: string, requestId?: string): Promise<ChatAnswer> {
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
    return parsed.data;
  },

  async resetSession(): Promise<void> {
    await request("/api/session", { method: "DELETE" });
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
