"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Activity, ArrowLeft, BookOpen, CheckCircle2, Clock, ExternalLink, Loader2, LogOut, RefreshCw, ShieldCheck, Users } from "lucide-react";
import { staffApi, StaffApiError, type AISummary, type StaffConfig, type StaffReview, type StaffSource } from "@/lib/staff-api";
import type { ConversationMessage, PatientSummaryResponse, UserProfile } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

function safeUrl(value: string) {
  try { const url = new URL(value); return url.protocol === "https:" ? url.href : undefined; }
  catch { return undefined; }
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : "Unable to complete this request."; }
const severityOrder: Record<string, number> = { emergency: 0, severe: 1, moderate: 2, general: 3 };

export default function StaffPage() {
  const [config, setConfig] = useState<StaffConfig | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [reviews, setReviews] = useState<StaffReview[]>([]);
  const [sources, setSources] = useState<StaffSource[]>([]);
  const [patients, setPatients] = useState<UserProfile[]>([]);
  const [summary, setSummary] = useState<PatientSummaryResponse | null>(null);
  const [patientId, setPatientId] = useState("");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [draft, setDraft] = useState("");
  const [aiSummary, setAiSummary] = useState<AISummary | null>(null);
  const sendId = useRef<string | null>(null);
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState("reviews");
  const [reviewFilter, setReviewFilter] = useState("open");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [today] = useState(() => new Date().toLocaleDateString("en-CA"));

  useEffect(() => {
    let active = true;
    staffApi.config().then(value => { if (active) setConfig(value); })
      .catch(error => { if (active) setError(errorMessage(error)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!token || !patientId || !summary || busy) return;
    let active = true;
    const timer = setInterval(() => {
      void staffApi.messages(token, patientId).then(history => {
        if (active) setMessages(history);
      }).catch(error => {
        if (!active) return;
        if (error instanceof StaffApiError && [401, 403].includes(error.status)) {
          setToken(""); setMessages([]); setSummary(null); setAiSummary(null); setPatients([]); setReviews([]); setSources([]);
        }
        setError(errorMessage(error));
      });
    }, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [token, patientId, summary, busy]);

  function clearWorkspace() {
    setToken(""); setReviews([]); setSources([]); setPatients([]); setSummary(null);
    setPatientId(""); setLastUpdated(null); setNotice(""); setMessages([]); setConversationId(""); setDraft(""); setAiSummary(null);
  }
  function selectPatient(id: string) {
    setPatientId(id); setSummary(null); setAiSummary(null); setMessages([]); setConversationId(""); setDraft(""); sendId.current = null;
  }
  async function loadPatient(id = patientId) {
    const [report, history] = await Promise.all([staffApi.summary(token, id, days), staffApi.messages(token, id)]);
    setSummary(report); setMessages(history); setConversationId(history.at(-1)?.conversation_id ?? ""); setAiSummary(null);
  }
  function sendDoctorMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim()) return;
    sendId.current ??= crypto.randomUUID();
    void run(async () => {
      const message = await staffApi.sendMessage(token, patientId, conversationId, draft, sendId.current!);
      setMessages(previous => previous.some(m => m.id === message.id) ? previous : [...previous, message]);
      setDraft(""); sendId.current = null;
    }, "Doctor message sent to the patient's conversation.");
  }
  async function refresh(accessToken = token) {
    const [nextReviews, nextSources, nextPatients] = await Promise.all([
      staffApi.reviews(accessToken), staffApi.sources(accessToken), staffApi.patients(accessToken),
    ]);
    setReviews(nextReviews); setSources(nextSources); setPatients(nextPatients); setLastUpdated(new Date());
  }
  async function run(action: () => Promise<void>, success = "") {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); }
    catch (error) {
      if (error instanceof StaffApiError && [401, 403].includes(error.status)) clearWorkspace();
      setError(errorMessage(error));
    } finally { setBusy(false); }
  }
  function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    void run(async () => {
      if (!config) throw new Error("Wait for the hospital configuration to load.");
      const accessToken = config.local_staff ? String(values.get("token") ?? "").trim()
        : await staffApi.signIn(config, String(values.get("email")), String(values.get("password")));
      if (!accessToken) throw new Error("Enter your staff access token.");
      await refresh(accessToken); setToken(accessToken); form.reset();
    }, "Connected to the staff workspace.");
  }
  function addSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    void run(async () => {
      await staffApi.addSource(token, {
        title: String(values.get("title")), url: String(values.get("url")), text: String(values.get("text")),
        checked_at: String(values.get("checked_at")), category: String(values.get("category")),
      });
      await refresh(); form.reset();
    }, "Source staged for review. Approve it after checking its content.");
  }
  const openReviews = reviews.filter(review => review.status !== "replied");
  const urgent = openReviews.filter(review => review.status === "urgent" || review.severity === "emergency");
  const visibleReviews = reviews.filter(review => reviewFilter === "all" ||
    (reviewFilter === "replied" ? review.status === "replied" : review.status !== "replied"))
    .sort((a, b) => (severityOrder[a.severity ?? "general"] ?? 3) - (severityOrder[b.severity ?? "general"] ?? 3) || b.created - a.created);
  const visibleSources = sources.filter(source =>
    (sourceFilter === "all" || (sourceFilter === "approved" ? source.approved : !source.approved)) &&
    `${source.title} ${source.category}`.toLowerCase().includes(search.toLowerCase()));
  const totalPages = Math.max(1, Math.ceil(visibleSources.length / 20));
  const currentPage = Math.min(page, totalPages - 1);

  return <div className="min-h-dvh bg-background text-foreground">
    <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-4 sm:px-8">
        <div className="flex items-center gap-3"><div className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground"><ShieldCheck /></div>
          <div><p className="font-semibold tracking-tight">MediAgent <span className="font-normal text-muted-foreground">/ Staff</span></p>
            <p className="text-xs text-muted-foreground">{config?.hospital ?? "Hospital workspace"}</p></div></div>
        <div className="flex items-center gap-2">
          <Link href="/" className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"><ArrowLeft className="size-4" /><span className="hidden sm:inline">Patient chat</span></Link>
          {token && <><Button variant="outline" disabled={busy} onClick={() => void run(() => refresh(), "Workspace updated.")} aria-label="Refresh workspace"><RefreshCw data-icon="inline-start" /><span className="hidden sm:inline">Refresh</span></Button>
            <Button variant="ghost" disabled={busy} onClick={clearWorkspace} aria-label="Sign out"><LogOut data-icon="inline-start" /></Button></>}
        </div>
      </div>
    </header>
    <main className="mx-auto max-w-7xl px-4 py-8 sm:px-8 sm:py-12">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div><p className="mb-2 text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Care coordination</p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Staff workspace</h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">Review patient requests, follow symptom timelines, and manage the sources behind hospital guidance.</p></div>
        {lastUpdated && <p className="flex items-center gap-2 text-xs text-muted-foreground"><Clock className="size-3.5" />Updated {lastUpdated.toLocaleTimeString()}</p>}
      </div>
      {error && <Alert variant="destructive" className="mb-6"><AlertTitle>Unable to complete request</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <Alert className="mb-6"><CheckCircle2 /><AlertTitle>Workspace update</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}
      {!token ? <Card className="mx-auto max-w-md"><CardHeader><CardTitle>Staff sign-in</CardTitle><CardDescription>{config?.local_staff ? "Use your local ADMIN_TOKEN. Access is verified by the server." : "Sign in with your authorised hospital staff account."}</CardDescription></CardHeader>
        <CardContent><form onSubmit={signIn}><fieldset disabled={busy || !config}><FieldGroup>
          {config?.local_staff ? <Field><FieldLabel htmlFor="staff-token">Staff access token</FieldLabel><Input id="staff-token" name="token" type="password" required autoComplete="off" /></Field>
            : <><Field><FieldLabel htmlFor="staff-email">Work email</FieldLabel><Input id="staff-email" name="email" type="email" required autoComplete="username" /></Field>
              <Field><FieldLabel htmlFor="staff-password">Password</FieldLabel><Input id="staff-password" name="password" type="password" required autoComplete="current-password" /></Field></>}
          <Button type="submit" disabled={busy || !config}>{busy && <Loader2 data-icon="inline-start" className="animate-spin" />}{busy ? "Connecting…" : "Open workspace"}</Button>
        </FieldGroup></fieldset></form><p className="mt-4 text-xs leading-5 text-muted-foreground">Staff credentials remain in memory and are cleared when you sign out or reload.</p></CardContent></Card>
        : <>
          <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[{ label: "Awaiting review", value: openReviews.length, icon: Clock }, { label: "Urgent requests", value: urgent.length, icon: Activity },
              { label: "Approved sources", value: sources.filter(s => s.approved && !s.conflict).length, icon: BookOpen }, { label: "Registered patients", value: patients.length, icon: Users }]
              .map(item => <Card key={item.label} size="sm"><CardHeader><CardDescription className="flex items-center justify-between gap-2">{item.label}<item.icon className="size-4" /></CardDescription><CardTitle className="text-3xl tabular-nums">{item.value}</CardTitle></CardHeader></Card>)}
          </div>
          <Tabs value={tab} onValueChange={value => setTab(String(value))} className="gap-6">
            <TabsList><TabsTrigger value="reviews">Review queue</TabsTrigger><TabsTrigger value="patients">Patients & conversations</TabsTrigger><TabsTrigger value="sources">Knowledge sources</TabsTrigger></TabsList>
            <TabsContent value="reviews">
              <div className="mb-5 flex items-center justify-between gap-3"><h2 className="text-xl font-semibold tracking-tight">Patient requests</h2>
                <label className="flex items-center gap-2 text-sm text-muted-foreground">Show<select aria-label="Filter reviews" className="rounded-lg border border-input bg-background p-2" value={reviewFilter} onChange={e => setReviewFilter(e.target.value)}><option value="open">Open</option><option value="replied">Replied</option><option value="all">All requests</option></select></label></div>
              <div className="grid gap-4 lg:grid-cols-2">
                {!visibleReviews.length && <Card className="lg:col-span-2"><CardHeader><CardTitle>Nothing waiting here</CardTitle><CardDescription>Requests and alerts will appear in this queue. Refresh to check for updates.</CardDescription></CardHeader></Card>}
                {visibleReviews.map(review => <Card key={review.id} className="self-start"><CardHeader>
                  <div className="mb-2 flex items-center justify-between gap-2"><Badge variant={review.status !== "replied" && (review.status === "urgent" || review.severity === "emergency") ? "destructive" : "secondary"}>{review.status}</Badge>
                    <span className="text-xs text-muted-foreground">{new Date(review.created * 1000).toLocaleString()}</span></div>
                  <CardTitle>{review.summary || "Patient requested review"}</CardTitle><CardDescription>{review.question}</CardDescription></CardHeader>
                  <CardContent className="flex flex-col gap-4">
                    {review.user && <div className="rounded-lg bg-muted/50 p-3 text-sm"><p className="font-medium">{review.user.name}</p><p className="text-muted-foreground">{review.user.email}</p>
                      {review.user.phone && <p className="text-muted-foreground">Phone: {review.user.phone}</p>}
                      {(review.user.emergency_contact || review.user.emergency_phone) && <p className="mt-2">Emergency contact: {review.user.emergency_contact} {review.user.emergency_phone}</p>}
                      <Button variant="link" disabled={busy} onClick={() => { selectPatient(review.user!.id); setTab("patients"); void run(() => loadPatient(review.user!.id)); }}>Open patient conversation</Button></div>}
                    {review.reply ? <div className="rounded-lg border border-border p-3"><p className="mb-1 text-xs font-medium text-muted-foreground">Staff response</p><p className="whitespace-pre-wrap text-sm leading-6">{review.reply}</p></div>
                      : <form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const reply = String(new FormData(form).get("reply")); void run(async () => { await staffApi.reply(token, review.id, reply); await refresh(); }, "Reply saved for the patient."); }}>
                        <FieldGroup><Field><FieldLabel htmlFor={`reply-${review.id}`}>General guidance</FieldLabel><Textarea id={`reply-${review.id}`} name="reply" required minLength={3} maxLength={2000} rows={3} disabled={busy} placeholder="Write a clear, general-information response…" /></Field><Button type="submit" disabled={busy}>Send response</Button></FieldGroup></form>}
                  </CardContent></Card>)}
              </div>
            </TabsContent>
            <TabsContent value="patients">
              <Card><CardHeader><CardTitle>Patient symptom timeline</CardTitle><CardDescription>Review recorded events and their progression before a conversation with the patient.</CardDescription></CardHeader><CardContent>
                <form className="mb-6 flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void run(() => loadPatient()); }}>
                  <Field className="min-w-48 flex-1"><FieldLabel htmlFor="patient">Patient</FieldLabel><select id="patient" required value={patientId} onChange={e => selectPatient(e.target.value)} className="rounded-lg border border-input bg-background p-2 text-sm" disabled={busy}><option value="">Choose a registered patient</option>{patients.map(patient => <option key={patient.id} value={patient.id}>{patient.name} — {patient.email}</option>)}</select></Field>
                  <Field className="w-32"><FieldLabel htmlFor="days">Period</FieldLabel><select id="days" value={days} onChange={e => { setDays(Number(e.target.value)); setSummary(null); setAiSummary(null); }} className="rounded-lg border border-input bg-background p-2 text-sm" disabled={busy}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select></Field>
                  <Button type="submit" disabled={busy || !patientId}>Load patient</Button>
                </form>
                {!summary && <p className="py-8 text-center text-sm text-muted-foreground">{busy ? "Loading patient records…" : "Select a patient to review their recorded symptom history."}</p>}
                {summary && <div className="flex flex-col gap-5"><div className="rounded-xl bg-muted/50 p-4"><p className="font-semibold">{summary.patient.name}</p><p className="mt-1 text-sm text-muted-foreground">{summary.total_events} events · Highest recorded severity: {summary.peak_severity}</p><p className="mt-3 text-sm leading-6">{summary.clinical_progression}</p></div>
                  <Button className="self-start" disabled={busy || !summary.total_events} onClick={() => void run(async () => { setAiSummary(await staffApi.generateSummary(token, patientId, days)); })}>Generate AI timeline summary</Button>
                  {aiSummary && <div className="rounded-xl border border-primary/30 bg-primary/5 p-5"><Badge variant="outline">AI draft · clinician review required</Badge><p className="mt-3 whitespace-pre-wrap text-sm leading-7">{aiSummary.text}</p><p className="mt-3 text-xs text-muted-foreground">Based on {aiSummary.event_count} recorded events{aiSummary.truncated ? " (latest 100 only)" : ""}. Reported symptoms are not confirmed findings.</p></div>}
                  <div className="grid items-start gap-6 xl:grid-cols-2"><section className="min-w-0"><h3 className="mb-4 font-semibold">Conversation history</h3>
                    <label className="mb-4 flex flex-col gap-2 text-sm">Conversation<select className="rounded-lg border border-input bg-background p-2" value={conversationId} disabled={busy} onChange={e => { setConversationId(e.target.value); setDraft(""); sendId.current = null; }}><option value="">Select conversation</option>{Array.from(new Set(messages.map(m => m.conversation_id))).map(id => <option key={id} value={id}>{new Date(messages.find(m => m.conversation_id === id)!.timestamp * 1000).toLocaleString()}</option>)}</select></label>
                    <div role="log" aria-label="Patient conversation" className="chat-scroll flex max-h-[480px] flex-col gap-3 overflow-y-auto rounded-xl border border-border p-4">{messages.filter(m => m.conversation_id === conversationId).map(message => <article key={message.id} className={`rounded-lg p-3 ${message.role === "staff" ? "bg-primary/10" : "bg-muted/50"}`}><div className="flex justify-between gap-3 text-xs text-muted-foreground"><span className="font-semibold">{message.role === "user" ? "Patient" : message.role === "staff" ? "Doctor / staff" : "MediAgent AI"}</span><time>{new Date(message.timestamp * 1000).toLocaleString()}</time></div><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{message.text}</p></article>)}{!messages.length && <p className="text-sm text-muted-foreground">No retained chat messages. New signed-in chats appear here.</p>}</div>
                    <form onSubmit={sendDoctorMessage} className="mt-4"><FieldGroup><Field><FieldLabel htmlFor="doctor-message">Doctor message</FieldLabel><Textarea id="doctor-message" value={draft} onChange={e => { setDraft(e.target.value); sendId.current = null; }} required maxLength={2000} disabled={busy || !conversationId} rows={3} placeholder="Send a message into this patient's chat…" /></Field><Button type="submit" disabled={busy || !conversationId || !draft.trim()}>Send to patient</Button></FieldGroup></form>
                  </section><section className="min-w-0"><h3 className="mb-4 font-semibold">Reported symptom timeline</h3>
                  {summary.timeline.map(item => <article key={item.id} className="border-l-2 border-border py-2 pl-4"><div className="flex flex-wrap items-center gap-2"><Badge variant={item.severity === "emergency" ? "destructive" : "secondary"}>{item.severity}</Badge><time className="text-xs text-muted-foreground">{new Date(item.timestamp * 1000).toLocaleString()}</time></div><p className="mt-2 text-sm font-medium">{item.summary}</p><p className="mt-1 text-sm text-muted-foreground">{item.query}</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6">{item.answer_snippet}</p></article>)}
                  {!summary.timeline.length && <p className="text-sm text-muted-foreground">No events recorded for this period.</p>}
                  </section></div>
                </div>}
              </CardContent></Card>
            </TabsContent>
            <TabsContent value="sources">
              <div className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
                <div className="min-w-0"><div className="mb-5 flex flex-wrap gap-3"><Input aria-label="Search sources" placeholder="Search by title or category…" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} className="min-w-40 flex-1" />
                  <select aria-label="Filter source approval" value={sourceFilter} onChange={e => { setSourceFilter(e.target.value); setPage(0); }} className="rounded-lg border border-input bg-background px-3 text-sm"><option value="all">All sources</option><option value="pending">Pending</option><option value="approved">Approved</option></select></div>
                  <div className="flex flex-col gap-3">{visibleSources.slice(currentPage * 20, (currentPage + 1) * 20).map(source => <Card key={source.id} size="sm"><CardHeader><div className="mb-2 flex flex-wrap gap-2"><Badge variant="secondary">{source.category}</Badge><Badge variant={source.conflict ? "destructive" : "outline"}>{source.conflict ? "Conflict · excluded" : source.approved ? "Approved" : "Pending"}</Badge></div><CardTitle>{source.title}</CardTitle><CardDescription>Checked {source.checked_at} · Revision {source.revision ?? 1}</CardDescription></CardHeader><CardContent>
                    {safeUrl(source.url) && <a href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer" className="mb-3 inline-flex max-w-full items-center gap-2 break-all text-xs text-muted-foreground underline underline-offset-4">{source.url}<ExternalLink className="size-3 shrink-0" /></a>}
                    <details className="mb-4"><summary className="cursor-pointer text-sm">Read source content</summary><p className="mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap text-sm leading-6">{source.text}</p></details>
                    <Button variant={source.approved ? "outline" : "default"} disabled={busy} onClick={() => void run(async () => { await staffApi.approve(token, source.id, !source.approved); await refresh(); }, source.approved ? "Source withdrawn." : "Source approved.")}>{source.approved ? "Withdraw source" : "Approve source"}</Button>
                  </CardContent></Card>)}
                    {!visibleSources.length && <p className="py-12 text-center text-sm text-muted-foreground">No sources match this filter.</p>}
                  </div>
                  <div className="mt-5 flex items-center justify-between gap-3"><Button variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</Button><span className="text-xs text-muted-foreground">{visibleSources.length} sources · {currentPage + 1} / {totalPages}</span><Button variant="outline" disabled={currentPage + 1 >= totalPages} onClick={() => setPage(currentPage + 1)}>Next</Button></div>
                </div>
                <Card><CardHeader><CardTitle>Add a public source</CardTitle><CardDescription>New sources enter the queue for approval.</CardDescription></CardHeader><CardContent><form onSubmit={addSource}><fieldset disabled={busy}><FieldGroup>
                  <Field><FieldLabel htmlFor="source-title">Title</FieldLabel><Input id="source-title" name="title" required minLength={3} maxLength={180} /></Field>
                  <Field><FieldLabel htmlFor="source-url">Source URL</FieldLabel><Input id="source-url" name="url" type="url" required pattern="https://.*" placeholder="https://…" /></Field>
                  <Field><FieldLabel htmlFor="source-category">Category</FieldLabel><select id="source-category" name="category" className="rounded-lg border border-input bg-background p-2 text-sm"><option value="hospital">Hospital</option><option value="health">Health education</option></select></Field>
                  <Field><FieldLabel htmlFor="source-date">Checked on</FieldLabel><Input id="source-date" name="checked_at" type="date" required defaultValue={today} max={today} /></Field>
                  <Field><FieldLabel htmlFor="source-text">Public content</FieldLabel><Textarea id="source-text" name="text" required minLength={30} maxLength={60000} rows={6} placeholder="Paste public information with its original context…" /></Field>
                  <Button type="submit" disabled={busy}>Stage source</Button>
                </FieldGroup></fieldset></form></CardContent></Card>
              </div>
            </TabsContent>
          </Tabs>
          {busy && <p role="status" className="mt-5 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Updating workspace…</p>}
        </>}
      <p className="mt-12 text-xs leading-5 text-muted-foreground">Independent hospital-information demo. This workspace is not an emergency dispatch service.</p>
    </main>
  </div>;
}
