import { z } from "zod";

export const UserProfileSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  name: z.string(),
  phone: z.string().optional().default(""),
  age: z.number().nullable().optional(),
  gender: z.string().optional().default(""),
  emergency_contact: z.string().optional().default(""),
  emergency_phone: z.string().optional().default(""),
  created_at: z.number().optional(),
});

export type UserProfile = z.infer<typeof UserProfileSchema>;

export const SourceCitationSchema = z.object({
  id: z.string(),
  title: z.string(),
  url: z.string(),
  checked_at: z.string(),
  page: z.number().nullish(),
});

export type SourceCitation = z.infer<typeof SourceCitationSchema>;

export const SeveritySchema = z.object({
  level: z.enum(["general", "moderate", "severe", "emergency"]),
  confidence: z.number().optional().default(0.9),
});

export type Severity = z.infer<typeof SeveritySchema>;

export const ChatAnswerSchema = z.object({
  text: z.string(),
  sources: z.array(SourceCitationSchema).default([]),
  evidence: z.string().optional(),
  route: z.string().optional(),
  mode: z.string().optional(),
  guarded: z.boolean().optional(),
  severity: SeveritySchema.nullish(),
  review_id: z.string().nullish(),
});

export type ChatAnswer = z.infer<typeof ChatAnswerSchema>;

export const ConversationMessageSchema = z.object({
  id: z.string(), conversation_id: z.string(), user_id: z.string(),
  role: z.enum(["user", "assistant", "staff"]), text: z.string(), timestamp: z.number(),
  answer: ChatAnswerSchema.nullish(),
});
export type ConversationMessage = z.infer<typeof ConversationMessageSchema>;

export const SessionHistorySchema = z.object({
  messages: z.array(ConversationMessageSchema).default([]),
  turns: z.array(z.object({
    question: z.string(),
    answer: z.unknown(),
    created: z.number().optional(),
  })).default([]),
});

export function parseSessionHistory(data: unknown) {
  const session = SessionHistorySchema.parse(data);
  return { messages: session.messages, turns: session.turns.map((turn) => {
    const parsed = ChatAnswerSchema.safeParse(turn.answer);
    // Older records may have unsupported metadata. Preserve only valid text,
    // never coerce an object into a message string.
    const textOnly = z.object({ text: z.string() }).safeParse(turn.answer);
    const answer: ChatAnswer = parsed.success ? parsed.data : {
      text: textOnly.success ? textOnly.data.text : "This earlier answer could not be restored. Please ask again.",
      sources: [],
      evidence: "none",
    };
    return { question: turn.question, answer, created: turn.created };
  }) };
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "staff";
  text: string;
  answer?: ChatAnswer;
  timestamp: number;
}

export const SymptomEventSchema = z.object({
  id: z.string(),
  timestamp: z.number(),
  query: z.string(),
  summary: z.string(),
  severity: z.enum(["emergency", "severe", "moderate", "general"]),
  answer_snippet: z.string(),
});

export type SymptomEvent = z.infer<typeof SymptomEventSchema>;

export const PatientSummaryResponseSchema = z.object({
  patient: UserProfileSchema,
  since_days: z.number(),
  total_events: z.number(),
  peak_severity: z.string(),
  severity_counts: z.record(z.string(), z.number()),
  clinical_progression: z.string(),
  timeline: z.array(SymptomEventSchema),
});

export type PatientSummaryResponse = z.infer<typeof PatientSummaryResponseSchema>;

export const ConfigResponseSchema = z.object({
  hospital: z.string(),
  portal_url: z.string().optional().default(""),
  auth_configured: z.boolean().optional().default(true),
});

export type ConfigResponse = z.infer<typeof ConfigResponseSchema>;
