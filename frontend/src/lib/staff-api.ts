import { z } from "zod";
import { ConversationMessageSchema, PatientSummaryResponseSchema, UserProfileSchema } from "@/types/api";

export const StaffReviewSchema = z.object({
  id: z.string(), question: z.string(), status: z.string(), created: z.number(),
  severity: z.string().optional(), summary: z.string().optional(), confidence: z.number().optional(),
  reply: z.string().nullish(), user: UserProfileSchema.nullish(),
});
export type StaffReview = z.infer<typeof StaffReviewSchema>;
export const StaffSourceSchema = z.object({
  id: z.string(), title: z.string(), text: z.string(), url: z.string(),
  category: z.enum(["hospital", "health"]), checked_at: z.string(), approved: z.boolean(),
  conflict: z.boolean().optional(), revision: z.number().optional(),
});
export type StaffSource = z.infer<typeof StaffSourceSchema>;
export const StaffConfigSchema = z.object({
  hospital: z.string(), local_staff: z.boolean().default(false),
  supabase_url: z.string().default(""), supabase_anon_key: z.string().default(""),
});
export type StaffConfig = z.infer<typeof StaffConfigSchema>;

export const AISummarySchema = z.object({ text: z.string(), event_count: z.number(), ai_generated: z.boolean(), truncated: z.boolean().optional() });
export type AISummary = z.infer<typeof AISummarySchema>;

export class StaffApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function request(path: string, token = "", options: RequestInit = {}): Promise<unknown> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "same-origin", cache: "no-store" });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new StaffApiError(typeof data?.error === "string" ? data.error
      : typeof data?.error_description === "string" ? data.error_description
      : `Request failed (${response.status}). Please retry.`, response.status);
  }
  return data;
}

export const staffApi = {
  async messages(token: string, userId: string) {
    return z.object({ messages: z.array(ConversationMessageSchema) }).parse(
      await request(`/api/staff/patients/${encodeURIComponent(userId)}/messages`, token)).messages;
  },
  async sendMessage(token: string, userId: string, conversationId: string, text: string, requestId: string) {
    return ConversationMessageSchema.parse(await request(`/api/staff/patients/${encodeURIComponent(userId)}/messages`, token,
      { method: "POST", body: JSON.stringify({ conversation_id: conversationId, text, request_id: requestId }) }));
  },
  async generateSummary(token: string, userId: string, days: number) {
    return AISummarySchema.parse(await request(`/api/staff/patients/${encodeURIComponent(userId)}/summary`, token,
      { method: "POST", body: JSON.stringify({ since_days: days }) }));
  },
  async config() { return StaffConfigSchema.parse(await request("/api/config")); },
  async reviews(token: string) { return z.array(StaffReviewSchema).parse(await request("/api/staff/reviews", token)); },
  async sources(token: string) { return z.array(StaffSourceSchema).parse(await request("/api/staff/sources", token)); },
  async patients(token: string) {
    return z.object({ patients: z.array(UserProfileSchema) }).parse(await request("/api/staff/patients", token)).patients;
  },
  async summary(token: string, userId: string, days: number) {
    const params = new URLSearchParams({ user_id: userId, since_days: String(days) });
    return PatientSummaryResponseSchema.parse(await request(`/api/staff/patient-summary?${params}`, token));
  },
  async reply(token: string, id: string, reply: string) {
    await request(`/api/staff/reviews/${encodeURIComponent(id)}/reply`, token,
      { method: "POST", body: JSON.stringify({ reply }) });
  },
  async approve(token: string, id: string, approved: boolean) {
    await request(`/api/staff/sources/${encodeURIComponent(id)}`, token,
      { method: "PATCH", body: JSON.stringify({ approved }) });
  },
  async addSource(token: string, source: {title: string; url: string; text: string; checked_at: string; category: string}) {
    await request("/api/staff/sources", token, { method: "POST", body: JSON.stringify(source) });
  },
  async signIn(config: StaffConfig, email: string, password: string) {
    if (!config.supabase_url || !config.supabase_anon_key) throw new Error("Cloud staff sign-in is not configured.");
    const data = await request(`${config.supabase_url}/auth/v1/token?grant_type=password`, "", {
      method: "POST", headers: { apikey: config.supabase_anon_key }, body: JSON.stringify({ email, password }),
    });
    return z.object({ access_token: z.string() }).parse(data).access_token;
  },
};
