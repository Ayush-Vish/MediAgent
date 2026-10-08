"use client";

import React, { useState, useRef, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatMessage, SourceCitation, UserProfile } from "@/types/api";
import { chatApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  AlertTriangle,
  ArrowUp,
  BookOpen,
  Calendar,
  Check,
  Copy,
  ExternalLink,
  Heart,
  HelpCircle,
  Loader2,
  RotateCcw,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

interface ChatInterfaceProps {
  user: UserProfile | null;
  onOpenAuth: () => void;
  hospitalName: string;
}

const STARTER_PROMPTS = [
  { text: "How can I book an appointment?", icon: Calendar, color: "text-emerald-500" },
  { text: "What documents should I bring for an appointment?", icon: BookOpen, color: "text-blue-500" },
  { text: "What are the patient helpdesk contact hours?", icon: HelpCircle, color: "text-purple-500" },
  { text: "What is general cough education?", icon: Heart, color: "text-rose-500" },
  { text: "What is the difference between paracetamol and Dolo?", icon: Sparkles, color: "text-amber-500" },
];

function createMessage(role: ChatMessage["role"], text: string, answer?: ChatMessage["answer"]): ChatMessage {
  return { id: crypto.randomUUID(), role, text, answer, timestamp: Date.now() };
}

export function ChatInterface({ user, onOpenAuth, hospitalName }: ChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("");
  const [ready, setReady] = useState(false);
  const [hasError, setHasError] = useState(false);
  const pendingRef = useRef<{ query: string; requestId: string } | null>(null);
  const busyRef = useRef(false);
  const streamRef = useRef<AbortController | null>(null);

  useEffect(() => () => streamRef.current?.abort(), []);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Initialize session and restore turns
  useEffect(() => {
    let isCancelled = false;
    const loadSession = async () => {
      try {
        const session = await chatApi.initSession();
        if (isCancelled) return;
        if (session.turns && session.turns.length > 0) {
          const restored: ChatMessage[] = [];
          session.turns.forEach((t, i) => {
            restored.push({
              id: `hist-u-${i}`,
              role: "user",
              text: t.question,
              timestamp: t.created ? t.created * 1000 : Date.now() - (session.turns.length - i) * 60000,
            });
            restored.push({
              id: `hist-a-${i}`,
              role: "assistant",
              text: t.answer.text,
              answer: t.answer,
              timestamp: t.created ? t.created * 1000 : Date.now() - (session.turns.length - i) * 60000 + 1000,
            });
          });
          if (session.messages.length) {
            const conversation = session.messages.map(m => ({ id: m.id, role: m.role, text: m.text, answer: m.answer ?? undefined, timestamp: m.timestamp * 1000 }));
            setMessages(conversation);
          } else {
            setMessages(restored);
          }
        } else {
          setMessages([]);
        }
        setReady(true);
      } catch (err: unknown) {
        if (!isCancelled) {
          setHasError(true);
          setStatusMessage(err instanceof Error ? err.message : "Unable to connect. Please refresh to retry.");
        }
      }
    };

    loadSession();
    return () => {
      isCancelled = true;
    };
  }, [user?.id]);

  // Only doctor messages are merged into the live chat; never replace a pending turn.
  useEffect(() => {
    if (!ready || !user) return;
    let active = true;
    const poll = async () => {
      try {
        const history = await chatApi.history();
        if (!active) return;
        const staff = history.messages.filter(m => m.role === "staff");
        setMessages(previous => {
          const ids = new Set(previous.map(m => m.id));
          const added = staff.filter(m => !ids.has(m.id)).map(m => ({
            id: m.id, role: m.role, text: m.text, timestamp: m.timestamp * 1000,
          }));
          return added.length ? [...previous, ...added].sort((a, b) => a.timestamp - b.timestamp) : previous;
        });
      } catch { /* Keep the conversation visible during temporary polling failures. */ }
    };
    const timer = setInterval(() => { void poll(); }, 5000);
    void poll();
    return () => { active = false; clearInterval(timer); };
  }, [ready, user]);

  // Auto-scroll on new message
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const handleSend = async (textToSend?: string) => {
    const query = (textToSend || input).trim();
    if (!query || busyRef.current || !ready) return;
    busyRef.current = true;
    setHasError(false);

    setInput("");
    const userMsg = createMessage("user", query);

    if (pendingRef.current?.query !== query) {
      pendingRef.current = { query, requestId: crypto.randomUUID() };
      setMessages((prev) => [...prev, userMsg]);
    }
    setLoading(true);
    setStatusMessage("Finding sources and preparing your answer…");

    const draftMessage = createMessage("assistant", "");
    const controller = new AbortController();
    streamRef.current = controller;

    try {
      const answer = await chatApi.streamMessage(query, pendingRef.current!.requestId, text => {
        setMessages(previous => {
          const others = previous.filter(message => message.id !== draftMessage.id);
          return text ? [...others, { ...draftMessage, text }] : others;
        });
        setStatusMessage(text ? "Generating answer…" : "Preparing your answer…");
      }, controller.signal);
      setMessages(previous => [...previous.filter(message => message.id !== draftMessage.id),
        { ...draftMessage, text: answer.text, answer }]);
      pendingRef.current = null;
      setStatusMessage("");
    } catch (err: unknown) {
      setMessages(previous => previous.filter(message => message.id !== draftMessage.id));
      const errorText = err instanceof Error ? err.message : "Failed to obtain hospital guidance.";
      setHasError(true);
      setInput(query);
      setStatusMessage(`${errorText} Your question is saved below so you can retry.`);
    } finally {
      streamRef.current = null;
      busyRef.current = false;
      setLoading(false);
      textareaRef.current?.focus();
    }
  };

  const handleReset = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setLoading(true);
    try {
      await chatApi.resetSession();
      setMessages([]);
      pendingRef.current = null;
      setInput("");
      setHasError(false);
      setStatusMessage("Conversation cleared.");
      setTimeout(() => setStatusMessage(""), 2000);
    } catch (err: unknown) {
      setHasError(true);
      setStatusMessage(err instanceof Error ? err.message : "Unable to clear this conversation.");
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  };

  const handleCopy = (id: string, text: string, sources?: SourceCitation[]) => {
    let full = text;
    if (sources && sources.length > 0) {
      full += "\n\nSources:\n" + sources.map((s) => `[${s.id}] ${s.url}`).join("\n");
    }
    navigator.clipboard.writeText(full).then(() => {
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    }).catch(() => {
      setHasError(true);
      setStatusMessage("Unable to copy. Please select and copy the answer manually.");
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col max-w-4xl mx-auto w-full px-4 sm:px-6">
      {/* Messages Scroll Area */}
      <div ref={scrollRef} role="log" aria-label="Conversation" aria-live="polite" className="chat-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-8 pr-2 flex flex-col gap-8">
        {messages.length === 0 ? (
          <div className="my-auto flex flex-col items-center justify-center text-center px-4 max-w-lg mx-auto py-12">
            <div className="size-16 rounded-2xl bg-primary/5 text-primary flex items-center justify-center font-bold text-3xl mb-4 border border-border/60 shadow-sm">
              m<span className="text-emerald-500 ml-0.5">+</span>
            </div>
            <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              How can I help you today?
            </h1>
            <p className="mt-2 text-xs sm:text-sm text-muted-foreground leading-relaxed">
              A little clarity for your next step. Ask about hospital services, medicines, or general health information.
            </p>

            {!user && (
              <div className="mt-5 p-3 rounded-xl border border-dashed border-border bg-muted/20 flex items-center gap-3 text-left">
                <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
                  <ShieldAlert className="size-4" />
                </div>
                <div className="flex-1">
                  <p className="text-xs font-semibold text-foreground">Sign in for Emergency Triage & Doctor History</p>
                  <p className="text-[11px] text-muted-foreground">Emergency contacts will be attached if urgent symptoms occur.</p>
                </div>
                <Button size="sm" variant="outline" onClick={onOpenAuth} className="text-xs shrink-0">
                  Sign In
                </Button>
              </div>
            )}

            {/* Starter Prompts */}
            <div className="mt-8 flex flex-wrap justify-center gap-2 max-w-xl">
              {STARTER_PROMPTS.map((prompt, idx) => {
                const Icon = prompt.icon;
                return (
                  <button
                    key={idx}
                    type="button"
                    disabled={!ready || loading}
                    onClick={() => handleSend(prompt.text)}
                    className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-background/60 hover:bg-muted/60 px-3.5 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-all hover:scale-[1.02]"
                  >
                    <Icon className={`size-3.5 ${prompt.color}`} />
                    <span>{prompt.text}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === "user";
            const answer = msg.answer;
            const severity = answer?.severity;

            return (
              <div
                key={msg.id}
                className={`chat-message flex shrink-0 gap-3 ${isUser ? "justify-end" : "justify-start"}`}
              >
                {!isUser && (
                  <Avatar className="size-8 shrink-0 border border-border/80 mt-0.5">
                    <AvatarFallback className="bg-primary text-primary-foreground font-black text-xs">
                      m+
                    </AvatarFallback>
                  </Avatar>
                )}

                <div
                  className={`min-w-0 flex flex-col gap-2 max-w-[88%] sm:max-w-[85%] ${
                    isUser ? "items-end" : "items-start"
                  }`}
                >
                  {/* Message Label */}
                  <div className="flex items-center gap-2 text-[11px] text-muted-foreground px-1">
                    <span className="font-semibold text-foreground">
                      {isUser ? "You" : msg.role === "staff" ? "Hospital Staff" : "MediAgent"}
                    </span>
                    <span>·</span>
                    <span>
                      {new Date(msg.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </div>

                  {/* Severity Banner */}
                  {!isUser && severity && severity.level === "emergency" && (
                    <div className="w-full rounded-xl border border-destructive bg-destructive/15 text-destructive p-3.5 flex items-start gap-2.5 shadow-sm">
                      <ShieldAlert className="size-5 shrink-0 text-destructive mt-0.5" />
                      <div className="flex-1 text-xs">
                        <div className="font-bold flex items-center gap-1.5 text-destructive text-sm">
                          🚨 IMMEDIATE EMERGENCY ALERT
                          <Badge variant="destructive" className="text-[10px] ml-auto">
                            {Math.round((severity.confidence || 0.95) * 100)}% Confidence
                          </Badge>
                        </div>
                        <p className="mt-1 text-foreground/90 font-medium">
                          These symptoms warrant prompt medical emergency care.
                          {user && ` Escalated for patient: ${user.name}.`}
                        </p>
                      </div>
                    </div>
                  )}

                  {!isUser && severity && severity.level === "severe" && (
                    <div className="w-full rounded-xl border border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400 p-3 flex items-start gap-2.5 shadow-sm">
                      <AlertTriangle className="size-4 shrink-0 mt-0.5 text-amber-500" />
                      <div className="flex-1 text-xs">
                        <div className="font-semibold flex items-center justify-between">
                          <span>⚠️ Severe Clinical Concern</span>
                          <span className="text-[10px]">{Math.round((severity.confidence || 0.8) * 100)}% match</span>
                        </div>
                        <p className="mt-0.5 text-foreground/85">
                          Triage recommends medical consultation without delay.
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Message Bubble */}
                  <div
                    className={`min-w-0 max-w-full rounded-2xl px-4 py-3 text-[15px] leading-7 ${
                      isUser
                        ? "bg-primary text-primary-foreground rounded-br-none shadow-sm"
                        : "bg-muted/40 border border-border/60 text-foreground rounded-bl-none"
                    }`}
                  >
                    {isUser ? <p className="whitespace-pre-wrap break-words">{
                      msg.text === "[Message not retained for privacy]" ? "Earlier question hidden for privacy" : msg.text
                    }</p> : <div className="chat-markdown">
                      <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
                        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
                        table: ({ children }) => <div className="max-w-full overflow-x-auto"><table>{children}</table></div>,
                      }}>{msg.text}</Markdown>
                    </div>}
                  </div>

                  {/* Sources Chips */}
                  {loading && msg.role === "assistant" && !answer && <p className="px-1 text-xs text-muted-foreground">Draft · checking sources when complete</p>}
                  {!isUser && answer?.sources && answer.sources.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-1 px-0.5">
                      {answer.sources.map((src) => (
                        <a
                          key={src.id}
                          href={src.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 rounded-md border border-border/80 bg-background/80 hover:bg-muted/80 px-2.5 py-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors max-w-xs truncate"
                        >
                          <span className="font-semibold text-primary">[{src.id}]</span>
                          <span className="truncate">{src.title}</span>
                          <ExternalLink className="size-2.5 shrink-0 ml-0.5" />
                        </a>
                      ))}
                    </div>
                  )}

                  {/* Actions & Meta */}
                  {!isUser && (
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground mt-0.5 px-1">
                      <button
                        type="button"
                        onClick={() => handleCopy(msg.id, msg.text, answer?.sources)}
                        className="inline-flex items-center gap-1 hover:text-foreground transition-colors p-1 rounded hover:bg-muted"
                        title="Copy answer"
                      >
                        {copiedId === msg.id ? (
                          <>
                            <Check className="size-3 text-emerald-500" />
                            <span className="text-emerald-500">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="size-3" />
                            <span>Copy</span>
                          </>
                        )}
                      </button>
                      <span>·</span>
                      <span>{answer?.evidence === "supported" ? "Based on linked sources"
                        : answer?.route === "emergency" ? "Emergency guidance"
                        : answer?.evidence === "general" ? "General education · AI assisted" : "General guidance"}</span>
                    </div>
                  )}
                </div>

                {isUser && (
                  <Avatar className="size-8 shrink-0 border border-border/80 mt-0.5">
                    <AvatarFallback className="bg-muted text-muted-foreground font-semibold text-xs">
                      {user ? user.name.charAt(0).toUpperCase() : "You"}
                    </AvatarFallback>
                  </Avatar>
                )}
              </div>
            );
          })
        )}

        {/* Loading Indicator */}
        {loading && (
          <div className="flex gap-3 items-center text-xs text-muted-foreground py-2 px-1">
            <Loader2 className="size-4 animate-spin text-primary" />
            <span>{statusMessage || "MediAgent is verifying clinical information..."}</span>
          </div>
        )}
      </div>

      {/* Composer Section */}
      <div className="shrink-0 pt-3 pb-4 sm:pb-5">
        {!loading && statusMessage && <p role={hasError ? "alert" : "status"}
          className={`mb-3 rounded-xl border p-3 text-sm ${hasError ? "border-destructive/40 text-destructive" : "border-border text-muted-foreground"}`}>
          {statusMessage}
        </p>}
        <div className="relative rounded-2xl border border-border/80 bg-card/80 backdrop-blur-sm shadow-sm focus-within:border-primary/60 transition-all p-2 flex flex-col gap-2">
          <textarea
            ref={textareaRef}
            rows={2}
            aria-label="Message MediAgent"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading || !ready}
            placeholder={`Message MediAgent (e.g. describe symptoms or ask questions about ${hospitalName})...`}
            className="w-full resize-none bg-transparent px-2.5 py-1 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />

          <div className="flex items-center justify-between px-1">
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1">
                <Heart className="size-3 text-rose-500" />
                Hospital &amp; Symptom Guidance
              </span>
              {messages.length > 0 && (
                <>
                  <span>·</span>
                  <button
                    type="button"
                    onClick={handleReset}
                    className="hover:text-foreground inline-flex items-center gap-1 transition-colors"
                  >
                    <RotateCcw className="size-3" />
                    New Chat
                  </button>
                </>
              )}
            </div>

            <Button
              type="button"
              size="icon"
              aria-label="Send message"
              disabled={loading || !ready || !input.trim()}
              onClick={() => handleSend()}
              className="size-8 rounded-full bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40"
            >
              {loading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ArrowUp className="size-4" />
              )}
            </Button>
          </div>
        </div>

        <p className="mt-2 text-center text-[10px] text-muted-foreground">
          Independent clinical demo for {hospitalName}. For immediate life-threatening symptoms, dial emergency services directly.
        </p>
      </div>
    </div>
  );
}
