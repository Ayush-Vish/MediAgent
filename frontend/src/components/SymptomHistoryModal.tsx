"use client";

import React, { useState, useEffect } from "react";
import { PatientSummaryResponse } from "@/types/api";
import { symptomApi } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Activity,
  AlertTriangle,
  Calendar,
  Check,
  Clock,
  Copy,
  FileText,
  Loader2,
  Stethoscope,
} from "lucide-react";

interface SymptomHistoryModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SymptomHistoryModal({ open, onOpenChange }: SymptomHistoryModalProps) {
  const [days, setDays] = useState<number>(30);
  const [loading, setLoading] = useState<boolean>(false);
  const [summary, setSummary] = useState<PatientSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    if (!open) return;
    let isCancelled = false;

    const fetchSummary = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await symptomApi.getSummary(days);
        if (!isCancelled) {
          setSummary(res);
        }
      } catch (err: unknown) {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : "Failed to load symptom records");
        }
      } finally {
        if (!isCancelled) {
          setLoading(false);
        }
      }
    };

    fetchSummary();
    return () => {
      isCancelled = true;
    };
  }, [open, days]);

  const copyClinicalSummary = () => {
    if (!summary) return;
    const p = summary.patient;
    const s = summary;
    const daysLabel = s.since_days === 0 ? "All recorded consultations" : `Past ${s.since_days} days`;

    let text = `========================================================\n`;
    text += `PATIENT SYMPTOM PROGRESSION SUMMARY FOR DOCTOR APPOINTMENT\n`;
    text += `========================================================\n`;
    text += `Patient Name:      ${p.name}\n`;
    if (p.age) text += `Age / Gender:      ${p.age} ${p.gender ? `(${p.gender})` : ""}\n`;
    if (p.phone) text += `Contact Phone:     ${p.phone}\n`;
    if (p.emergency_contact) text += `Emergency Contact: ${p.emergency_contact} (${p.emergency_phone || "N/A"})\n`;
    text += `Reported Window:   ${daysLabel}\n`;
    text += `Total Consults:    ${s.total_events}\n`;
    text += `Peak Severity:     ${s.peak_severity.toUpperCase()} (Emergency: ${s.severity_counts.emergency || 0}, Severe: ${s.severity_counts.severe || 0}, Moderate: ${s.severity_counts.moderate || 0}, General: ${s.severity_counts.general || 0})\n`;
    text += `Clinical Trend:    ${s.clinical_progression}\n\n`;
    text += `--- Chronological Symptom Timeline ---\n`;

    summary.timeline.forEach((ev, idx) => {
      const timeStr = new Date(ev.timestamp * 1000).toLocaleString();
      text += `${idx + 1}. [${timeStr}] [${ev.severity.toUpperCase()}]\n`;
      text += `   Patient Query: "${ev.query}"\n`;
      text += `   Clinical Concern: ${ev.summary}\n`;
      if (ev.answer_snippet) {
        text += `   AI Clinical Advice: ${ev.answer_snippet.replace(/\n/g, " ").slice(0, 160)}...\n`;
      }
      text += `\n`;
    });

    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    });
  };

  const getSeverityBadge = (level: string) => {
    switch (level) {
      case "emergency":
        return <Badge variant="destructive" className="font-semibold uppercase tracking-wider text-[10px]">Emergency</Badge>;
      case "severe":
        return <Badge className="bg-amber-500 hover:bg-amber-600 text-white font-semibold uppercase tracking-wider text-[10px]">Severe</Badge>;
      case "moderate":
        return <Badge className="bg-blue-500 hover:bg-blue-600 text-white font-semibold uppercase tracking-wider text-[10px]">Moderate</Badge>;
      default:
        return <Badge variant="secondary" className="font-semibold uppercase tracking-wider text-[10px]">General</Badge>;
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[760px] max-h-[85vh] p-0 flex flex-col overflow-hidden border-border/80 bg-card">
        {/* Header */}
        <DialogHeader className="p-6 pb-4 border-b border-border/50">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500">
                <Stethoscope className="size-5" />
              </div>
              <div>
                <DialogTitle className="text-lg font-semibold tracking-tight">
                  Doctor Appointment Prep · Symptom History
                </DialogTitle>
                <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                  Chronological record of reported symptoms and clinical triage to share with your physician.
                </DialogDescription>
              </div>
            </div>
            {summary && (
              <Button
                variant="outline"
                size="sm"
                onClick={copyClinicalSummary}
                className="gap-1.5 text-xs border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10"
              >
                {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
                {copied ? "Copied Note!" : "Copy Summary for Doctor"}
              </Button>
            )}
          </div>
        </DialogHeader>

        {/* Filter Bar */}
        <div className="px-6 py-2.5 bg-muted/30 border-b border-border/40 flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-medium">
            <Calendar className="size-3.5" />
            <span>Time Window:</span>
          </div>
          <div className="flex items-center gap-1.5">
            {[7, 14, 30, 90, 0].map((d) => (
              <Button
                key={d}
                variant={days === d ? "default" : "outline"}
                size="sm"
                onClick={() => setDays(d)}
                className={`h-7 px-2.5 text-xs rounded-full ${
                  days === d ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {d === 0 ? "All Time" : `${d} Days`}
              </Button>
            ))}
          </div>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto flex-1 flex flex-col gap-5">
          {loading ? (
            <div className="py-16 flex flex-col items-center justify-center gap-3 text-muted-foreground">
              <Loader2 className="size-7 animate-spin text-primary" />
              <p className="text-xs">Aggregating patient symptom history...</p>
            </div>
          ) : error ? (
            <div className="py-12 text-center text-xs text-destructive">
              <AlertTriangle className="size-6 mx-auto mb-2" />
              {error}
            </div>
          ) : summary ? (
            <>
              {/* Metric Cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Card className="bg-muted/20 border-border/50 shadow-none">
                  <CardContent className="p-3.5 flex flex-col gap-1">
                    <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider">Patient</span>
                    <strong className="text-sm font-semibold truncate">{summary.patient.name}</strong>
                    <span className="text-[11px] text-muted-foreground truncate">
                      {[summary.patient.age ? `Age ${summary.patient.age}` : "", summary.patient.gender].filter(Boolean).join(" · ") || "Registered"}
                    </span>
                  </CardContent>
                </Card>

                <Card className="bg-muted/20 border-border/50 shadow-none">
                  <CardContent className="p-3.5 flex flex-col gap-1">
                    <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider">Consultations</span>
                    <strong className="text-sm font-semibold">{summary.total_events}</strong>
                    <span className="text-[11px] text-muted-foreground">
                      {days === 0 ? "All time records" : `In past ${days} days`}
                    </span>
                  </CardContent>
                </Card>

                <Card className="bg-muted/20 border-border/50 shadow-none">
                  <CardContent className="p-3.5 flex flex-col gap-1">
                    <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider">Peak Severity</span>
                    <div className="mt-0.5">{getSeverityBadge(summary.peak_severity)}</div>
                    <span className="text-[10px] text-muted-foreground mt-0.5">Highest triage level</span>
                  </CardContent>
                </Card>

                <Card className="bg-muted/20 border-border/50 shadow-none">
                  <CardContent className="p-3.5 flex flex-col gap-1">
                    <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider">Trajectory</span>
                    <strong className="text-xs font-semibold text-foreground line-clamp-1">{summary.clinical_progression}</strong>
                    <span className="text-[10px] text-muted-foreground">Clinical assessment</span>
                  </CardContent>
                </Card>
              </div>

              {/* Timeline */}
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs uppercase font-semibold text-muted-foreground tracking-wider flex items-center gap-1.5">
                    <Clock className="size-3.5" />
                    Chronological Symptom Consultations
                  </h3>
                  <span className="text-[11px] text-muted-foreground">{summary.timeline.length} records</span>
                </div>

                {summary.timeline.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-border/80 p-8 text-center text-xs text-muted-foreground">
                    No symptom consultations recorded in this time window. Describe any health symptoms in the chat to begin tracking.
                  </div>
                ) : (
                  <div className="flex flex-col gap-2.5">
                    {[...summary.timeline].reverse().map((item) => (
                      <div
                        key={item.id}
                        className={`rounded-xl border p-4 flex flex-col gap-2 transition-all ${
                          item.severity === "emergency"
                            ? "border-destructive/40 bg-destructive/5"
                            : item.severity === "severe"
                            ? "border-amber-500/40 bg-amber-500/5"
                            : "border-border/60 bg-muted/10 hover:border-border"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            {getSeverityBadge(item.severity)}
                            <span className="text-xs font-semibold text-foreground truncate max-w-[320px]">
                              &ldquo;{item.query}&rdquo;
                            </span>
                          </div>
                          <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                            {new Date(item.timestamp * 1000).toLocaleString(undefined, {
                              dateStyle: "medium",
                              timeStyle: "short",
                            })}
                          </span>
                        </div>

                        <div className="text-xs text-muted-foreground">
                          <span className="font-medium text-foreground">Clinical Concern:</span> {item.summary}
                        </div>

                        {item.answer_snippet && (
                          <div className="rounded-lg bg-background/80 border border-border/40 p-2.5 text-[11px] text-muted-foreground line-clamp-2">
                            <span className="font-medium text-foreground">Guidance provided:</span> {item.answer_snippet}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
