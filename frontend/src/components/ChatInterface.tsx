"use client";

import React, { useState, useRef, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatAnswer, ChatMessage, SourceCitation, UserProfile } from "@/types/api";
import { chatApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  ArrowUp,
  BookOpen,
  Calendar,
  Check,
  Copy,
  Heart,
  HelpCircle,
  Loader2,
  RotateCcw,
  Sparkles,
} from "lucide-react";

interface ChatInterfaceProps {
  user: UserProfile | null;
  onOpenAuth: () => void;
  hospitalName: string;
}

const STARTER_PROMPTS = [
  { text: "How can I book an appointment?", icon: Calendar },
  { text: "What documents should I bring for an appointment?", icon: BookOpen },
  { text: "What are the patient helpdesk contact hours?", icon: HelpCircle },
  { text: "What is general cough education?", icon: Heart },
  { text: "What is the difference between paracetamol and Dolo?", icon: Sparkles },
];

export const HEALTH_FACTS = [
  { fact: "Walking briskly for 30 minutes daily can lower cardiovascular disease risk by up to 35%.", source: "WHO" },
  { fact: "During deep sleep, the brain's glymphatic system flushes out toxic metabolic waste.", source: "NIH" },
  { fact: "About 70% of your body's immune cells reside within your gut microbiome.", source: "Johns Hopkins" },
  { fact: "Mild dehydration of just 1–2% body weight can impair focus, memory, and energy levels.", source: "CDC" },
  { fact: "Antibiotics only kill bacteria, not viruses—they do not cure common colds, flu, or viral sore throats.", source: "CDC" },
  { fact: "The 20-20-20 rule helps eye strain: every 20 minutes, look at an object 20 feet away for 20 seconds.", source: "AAO" },
  { fact: "Spending 20 minutes in green spaces or nature significantly lowers cortisol (stress hormone) levels.", source: "Harvard Health" },
  { fact: "Washing hands with soap and water for 20 seconds reduces diarrheal illnesses by up to 30%.", source: "CDC" },
  { fact: "A 2-minute movement break every 30 minutes of sitting helps stabilize blood sugar and insulin.", source: "Diabetes Care" },
  { fact: "Adults need 7–9 hours of consistent sleep each night for optimal immune and heart function.", source: "AHA" },
  { fact: "Your heart pumps roughly 7,500 liters (2,000 gallons) of blood through your circulatory system each day.", source: "Cleveland Clinic" },
  { fact: "Laughing increases endorphin release, relaxes blood vessels, and boosts oxygen intake.", source: "Mayo Clinic" },
  { fact: "Eating fiber-rich foods feeds beneficial gut microbes, which produce short-chain fatty acids that fight inflammation.", source: "Harvard T.H. Chan" },
  { fact: "Just 10–15 minutes of midday sunlight provides adequate Vitamin D synthesis for most skin types without burning.", source: "NHS" },
  { fact: "Chronic psychological stress can shorten telomeres, accelerating cellular aging.", source: "Nobel Prize / Blackburn Lab" },
  { fact: "Muscle tissue burns approximately 3 times more calories at rest than adipose (fat) tissue.", source: "NIH" },
  { fact: "Drinking water before meals can improve digestive enzyme efficiency and natural satiety signals.", source: "Mayo Clinic" },
  { fact: "Regular resistance training increases bone mineral density, reducing fracture risk later in life.", source: "National Osteoporosis Foundation" },
  { fact: "Dark leafy greens are loaded with lutein and zeaxanthin, which help protect retinal cells from blue light damage.", source: "NEI" },
  { fact: "The human cornea is one of the only tissues in the body that receives oxygen directly from the air rather than blood vessels.", source: "American Academy of Ophthalmology" },
  { fact: "Nose breathing filters, humidifies, and adds nitric oxide to inhaled air, enhancing oxygen uptake in the lungs.", source: "Stanford Medicine" },
  { fact: "Replacing sugary sodas with water or unsweetened tea significantly lowers type 2 diabetes risk.", source: "BMJ" },
  { fact: "Taking deep diaphragmatic breaths triggers the vagus nerve, immediately shifting your nervous system into 'rest and digest' mode.", source: "Harvard Health" },
  { fact: "Omega-3 fatty acids from fish, walnuts, or flaxseeds form crucial structural components of cell membranes in the brain.", source: "NIH" },
];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function streamWords(
  getTarget: () => string,
  isDone: () => boolean,
  hasError: () => unknown,
  signal: AbortSignal,
  onChunk: (text: string) => void
): Promise<string> {
  let displayed = "";
  while (!signal.aborted) {
    const error = hasError();
    if (error && !displayed) {
      throw error;
    }

    const targetText = getTarget();
    if (displayed.length < targetText.length) {
      if (!targetText.startsWith(displayed)) {
        let common = 0;
        while (
          common < displayed.length &&
          common < targetText.length &&
          displayed[common] === targetText[common]
        ) {
          common++;
        }
        displayed = displayed.slice(0, common);
      }

      const remaining = targetText.slice(displayed.length);
      const tokens = remaining.match(/(?:\n+|\s*\S+)/g) || [remaining];
      const token = tokens[0];
      displayed += token;
      onChunk(displayed);

      const remainingTokens = tokens.length;
      const delay = remainingTokens > 40 ? 8 : remainingTokens > 15 ? 18 : 28;
      await sleep(delay);
    } else if (isDone()) {
      if (error) {
        throw error;
      }
      break;
    } else {
      await sleep(25);
    }
  }
  return displayed;
}

function getRandomHealthFact(lastIndex: number): { fact: typeof HEALTH_FACTS[0]; index: number } {
  let nextIndex = Math.floor(Math.random() * HEALTH_FACTS.length);
  if (HEALTH_FACTS.length > 1 && nextIndex === lastIndex) {
    nextIndex = (nextIndex + 1) % HEALTH_FACTS.length;
  }
  return { fact: HEALTH_FACTS[nextIndex], index: nextIndex };
}

function createMessage(role: ChatMessage["role"], text: string, answer?: ChatMessage["answer"]): ChatMessage {
  return { id: crypto.randomUUID(), role, text, answer, timestamp: Date.now() };
}

export function ChatInterface({ user, onOpenAuth, hospitalName }: ChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("");
  const [processingStatus, setProcessingStatus] = useState<string>("");
  const [isDraftStreaming, setIsDraftStreaming] = useState<boolean>(false);
  const [currentFact, setCurrentFact] = useState<typeof HEALTH_FACTS[0] | null>(
    () => HEALTH_FACTS[Math.floor(Math.random() * HEALTH_FACTS.length)]
  );
  const [factFade, setFactFade] = useState(false);
  const [ready, setReady] = useState(false);
  const [hasError, setHasError] = useState(false);
  const pendingRef = useRef<{ query: string; requestId: string } | null>(null);
  const busyRef = useRef(false);
  const streamRef = useRef<AbortController | null>(null);
  const lastFactIndexRef = useRef(-1);

  useEffect(() => () => streamRef.current?.abort(), []);

  useEffect(() => {
    if (ready && !loading) return;

    const interval = setInterval(() => {
      setFactFade(true);
      setTimeout(() => {
        const next = getRandomHealthFact(lastFactIndexRef.current);
        lastFactIndexRef.current = next.index;
        setCurrentFact(next.fact);
        setFactFade(false);
      }, 250);
    }, 4500);

    return () => clearInterval(interval);
  }, [loading, ready]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Initialize session and restore turns
  useEffect(() => {
    let isCancelled = false;
    const loadSession = async () => {
      try {
        const session = await chatApi.initSession();
        if (isCancelled) return;
        if (session.messages.length || session.turns.length > 0) {
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
    const initial = getRandomHealthFact(lastFactIndexRef.current);
    lastFactIndexRef.current = initial.index;
    setCurrentFact(initial.fact);
    setProcessingStatus("Starting medical search…");
    setIsDraftStreaming(false);
    setLoading(true);

    const draftMessage = createMessage("assistant", "");
    const controller = new AbortController();
    streamRef.current = controller;

    let targetText = "";
    let serverDone = false;
    let finalAnswer: ChatAnswer | null = null;
    let streamError: unknown = null;

    // Start background fetch from server
    void chatApi
      .streamMessage(
        query,
        pendingRef.current!.requestId,
        (text) => {
          targetText = text;
          if (text.trim()) setIsDraftStreaming(true);
        },
        (text) => {
          setProcessingStatus(text);
        },
        controller.signal
      )
      .then((answer) => {
        finalAnswer = answer;
        targetText = answer.text;
        serverDone = true;
      })
      .catch((err) => {
        streamError = err;
        serverDone = true;
      });

    try {
      await streamWords(
        () => targetText,
        () => serverDone,
        () => streamError,
        controller.signal,
        (text) => {
          setMessages((previous) => {
            const others = previous.filter((m) => m.id !== draftMessage.id);
            return [...others, { ...draftMessage, text }];
          });
        }
      );

      if (finalAnswer) {
        setMessages((previous) => [
          ...previous.filter((m) => m.id !== draftMessage.id),
          { ...draftMessage, text: (finalAnswer as ChatAnswer).text, answer: finalAnswer as ChatAnswer },
        ]);
      }
      pendingRef.current = null;
      setStatusMessage("");
    } catch (err: unknown) {
      setMessages((previous) => previous.filter((m) => m.id !== draftMessage.id));
      const errorText = err instanceof Error ? err.message : "Failed to obtain hospital guidance.";
      setHasError(true);
      setInput(query);
      setStatusMessage(`${errorText} Your question is saved below so you can retry.`);
    } finally {
      streamRef.current = null;
      busyRef.current = false;
      setLoading(false);
      setProcessingStatus("");
      setIsDraftStreaming(false);
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
    <div className="flex min-h-0 flex-1 flex-col max-w-3xl mx-auto w-full px-4 sm:px-6">
      {/* Messages Scroll Area */}
      <div
        ref={scrollRef}
        role="log"
        aria-label="Conversation"
        aria-live="polite"
        className="chat-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-6 pr-1 flex flex-col gap-6"
      >
        {!ready ? (
          <div className="my-auto flex flex-col items-center justify-center text-center px-4 max-w-lg mx-auto py-16">
            <div className="flex items-start gap-3 text-xs py-3 px-4 rounded-2xl border border-border/50 bg-muted/20 max-w-md shadow-xs">
              <Sparkles className="size-4 mt-0.5 shrink-0 text-foreground/80 animate-pulse" />
              <div
                className={`text-left leading-relaxed transition-all duration-200 ${
                  factFade ? "opacity-0 translate-y-1" : "opacity-100 translate-y-0"
                }`}
              >
                {currentFact ? (
                  <>
                    <span className="font-semibold text-foreground">Health Fact: </span>
                    <span className="text-foreground/90 font-medium">{currentFact.fact}</span>
                    <span className="ml-1.5 text-[10px] text-muted-foreground/60 font-medium">({currentFact.source})</span>
                  </>
                ) : (
                  <span className="text-muted-foreground font-medium">Loading medical information…</span>
                )}
              </div>
            </div>
            <p className="mt-3 text-[11px] text-muted-foreground/60 font-medium">
              Connecting and loading session…
            </p>
          </div>
        ) : messages.length === 0 ? (
          <div className="my-auto flex flex-col items-center justify-center text-center px-4 max-w-lg mx-auto py-16">
            <h1 className="text-2xl sm:text-3xl font-medium tracking-tight text-foreground">
              How can I help you today?
            </h1>
            <p className="mt-2 text-xs sm:text-sm text-muted-foreground leading-relaxed">
              Ask about appointments, hospital services, or general health guidance.
            </p>

            {/* Starter Prompts */}
            <div className="mt-8 flex flex-wrap justify-center gap-2 max-w-lg">
              {STARTER_PROMPTS.map((prompt, idx) => {
                const Icon = prompt.icon;
                return (
                  <button
                    key={idx}
                    type="button"
                    disabled={!ready || loading}
                    onClick={() => handleSend(prompt.text)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/20 hover:bg-muted/50 px-3.5 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-all"
                  >
                    <Icon className="size-3 opacity-60" />
                    <span>{prompt.text}</span>
                  </button>
                );
              })}
            </div>

            {!user && (
              <p className="mt-8 text-[11px] text-muted-foreground/60">
                Want emergency contact escalation?{" "}
                <button
                  type="button"
                  onClick={onOpenAuth}
                  className="underline underline-offset-4 hover:text-foreground transition-colors"
                >
                  Sign in
                </button>
              </p>
            )}
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === "user";
            const answer = msg.answer;
            const severity = answer?.severity;

            if (isUser) {
              return (
                <div key={msg.id} className="flex flex-col items-end gap-1 self-end max-w-[85%]">
                  <div className="rounded-2xl bg-muted px-4 py-2 text-[14px] leading-relaxed text-foreground">
                    <p className="whitespace-pre-wrap break-words">
                      {msg.text === "[Message not retained for privacy]"
                        ? "Earlier question hidden for privacy"
                        : msg.text}
                    </p>
                  </div>
                </div>
              );
            }

            return (
              <div key={msg.id} className="flex flex-col items-start gap-2 self-start w-full max-w-[95%]">
                {/* Severity Notices */}
                {severity && severity.level === "emergency" && (
                  <div className="w-full rounded-lg border-l-2 border-l-destructive bg-destructive/5 px-3 py-2.5 text-xs">
                    <div className="font-medium text-destructive flex items-center justify-between">
                      <span>Immediate Emergency Notice</span>
                      <span className="text-[10px] text-muted-foreground font-mono">
                        {Math.round((severity.confidence || 0.95) * 100)}% match
                      </span>
                    </div>
                    <p className="mt-1 text-muted-foreground leading-relaxed">
                      These symptoms warrant prompt emergency care.
                      {user && ` Escalated for patient: ${user.name}.`}
                    </p>
                  </div>
                )}

                {severity && severity.level === "severe" && (
                  <div className="w-full rounded-lg border-l-2 border-l-amber-500 bg-amber-500/5 px-3 py-2.5 text-xs">
                    <div className="font-medium text-amber-600 dark:text-amber-400 flex items-center justify-between">
                      <span>Clinical Concern</span>
                      <span className="text-[10px] text-muted-foreground font-mono">
                        {Math.round((severity.confidence || 0.8) * 100)}% match
                      </span>
                    </div>
                    <p className="mt-1 text-muted-foreground leading-relaxed">
                      Medical consultation is advised without delay.
                    </p>
                  </div>
                )}

                {/* Markdown text */}
                <div className="text-[14px] leading-relaxed text-foreground chat-markdown w-full">
                  <Markdown
                    remarkPlugins={[remarkGfm]}
                    skipHtml
                    components={{
                      a: ({ href, children }) => (
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          {children}
                        </a>
                      ),
                      table: ({ children }) => (
                        <div className="max-w-full overflow-x-auto my-2">
                          <table>{children}</table>
                        </div>
                      ),
                    }}
                  >
                    {msg.text}
                  </Markdown>
                  {loading && !answer && (
                    <span className="inline-block w-1.5 h-3.5 ml-1 bg-foreground/60 animate-pulse rounded-xs align-baseline" />
                  )}
                </div>

                {/* Source Citations */}
                {answer?.sources && answer.sources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {answer.sources.map((src) => (
                      <a
                        key={src.id}
                        href={src.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/20 hover:bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors max-w-xs truncate"
                      >
                        <span className="font-mono text-[10px] text-muted-foreground/50">[{src.id}]</span>
                        <span className="truncate">{src.title}</span>
                      </a>
                    ))}
                  </div>
                )}

                {/* Micro Action Bar */}
                {answer && (
                  <div className="flex items-center gap-2 text-[11px] text-muted-foreground/50 pt-0.5">
                    <button
                      type="button"
                      onClick={() => handleCopy(msg.id, msg.text, answer?.sources)}
                      className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
                      title="Copy response"
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
                    <span>
                      {answer.evidence === "supported"
                        ? "Verified sources"
                        : answer.route === "emergency"
                        ? "Emergency guidance"
                        : answer.evidence === "general"
                        ? "General education"
                        : "Hospital guidance"}
                    </span>
                    <span>·</span>
                    <button
                      type="button"
                      disabled={loading || !ready}
                      onClick={() => handleSend("Hinglish mein samjhao")}
                      className="hover:text-foreground transition-colors inline-flex items-center gap-1 opacity-70 hover:opacity-100"
                      title="Convert explanation to Hinglish"
                    >
                      <span>🌐 Hinglish</span>
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}

        {/* Animated Health Fact Box (No Spinner) */}
        {loading && !isDraftStreaming && (
          <div role="status" aria-live="polite" className="flex items-start gap-2.5 text-xs py-2.5 px-3.5 rounded-xl border border-border/40 bg-muted/20 max-w-lg transition-all duration-300">
            <Loader2 className="size-3.5 mt-0.5 shrink-0 text-foreground/80 animate-spin" />
            <div className="min-w-0">
              <div className="font-semibold text-foreground">{processingStatus || "Preparing your answer…"}</div>
              <div
              className={`leading-relaxed transition-all duration-200 ${
                factFade ? "opacity-0 translate-y-1" : "opacity-100 translate-y-0"
              }`}
              >
              {currentFact ? (
                <>
                  <span className="text-muted-foreground">While you wait: {currentFact.fact}</span>
                  <span className="ml-1.5 text-[10px] text-muted-foreground/60 font-medium">({currentFact.source})</span>
                </>
              ) : (
                <span className="text-muted-foreground">Loading medical information…</span>
              )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Composer Section */}
      <div className="shrink-0 pt-2 pb-4 sm:pb-6">
        {!loading && statusMessage && (
          <p
            role={hasError ? "alert" : "status"}
            className={`mb-2 rounded-lg border px-3 py-2 text-xs ${
              hasError
                ? "border-destructive/30 text-destructive bg-destructive/5"
                : "border-border/60 text-muted-foreground"
            }`}
          >
            {statusMessage}
          </p>
        )}

        <div className="relative rounded-2xl border border-border/60 bg-muted/20 focus-within:border-foreground/20 focus-within:bg-background focus-within:ring-1 focus-within:ring-foreground/5 transition-all p-2 flex flex-col gap-1.5">
          <textarea
            ref={textareaRef}
            rows={1}
            aria-label="Message MediAgent"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading || !ready}
            placeholder={`Ask ${hospitalName} guidance or describe symptoms…`}
            className="w-full resize-none bg-transparent px-2 py-1 text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none min-h-[36px] max-h-36"
          />

          <div className="flex items-center justify-between px-1 pt-0.5">
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground/50">
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={handleReset}
                  className="hover:text-foreground inline-flex items-center gap-1 transition-colors"
                >
                  <RotateCcw className="size-3" />
                  <span>Clear</span>
                </button>
              )}
            </div>

            <Button
              type="button"
              size="icon"
              aria-label="Send message"
              disabled={loading || !ready || !input.trim()}
              onClick={() => handleSend()}
              className="size-7 rounded-full bg-foreground text-background hover:opacity-90 disabled:opacity-20 transition-opacity"
            >
              {loading ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <ArrowUp className="size-3.5" />
              )}
            </Button>
          </div>
        </div>

        <p className="mt-2.5 text-center text-xs text-muted-foreground font-normal">
          Independent assistant for {hospitalName}. For medical emergencies, call <strong className="font-semibold text-foreground">112</strong> immediately.
        </p>
      </div>
    </div>
  );
}
