"use client";

import React, { useState } from "react";
import { UserProfile } from "@/types/api";
import { authApi } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle, HeartPulse, Loader2, ShieldAlert } from "lucide-react";

interface AuthModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: (user: UserProfile) => void;
}

export function AuthModal({ open, onOpenChange, onSuccess }: AuthModalProps) {
  const [tab, setTab] = useState<string>("login");
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Login inputs
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");

  // Register inputs
  const [regName, setRegName] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regPhone, setRegPhone] = useState("");
  const [regAge, setRegAge] = useState("");
  const [regGender, setRegGender] = useState("");
  const [regEmergencyContact, setRegEmergencyContact] = useState("");
  const [regEmergencyPhone, setRegEmergencyPhone] = useState("");

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const user = await authApi.login(loginEmail.trim(), loginPassword);
      onSuccess(user);
      onOpenChange(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to sign in");
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const ageNum = regAge ? parseInt(regAge, 10) : null;
      const user = await authApi.register({
        name: regName.trim(),
        email: regEmail.trim(),
        password: regPassword,
        phone: regPhone.trim(),
        age: ageNum,
        gender: regGender,
        emergency_contact: regEmergencyContact.trim(),
        emergency_phone: regEmergencyPhone.trim(),
      });
      onSuccess(user);
      onOpenChange(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create patient account");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px] p-0 overflow-hidden border-border/80 bg-card">
        <DialogHeader className="p-6 pb-2">
          <div className="flex items-center gap-2 mb-1">
            <HeartPulse className="size-5 text-emerald-500" />
            <DialogTitle className="text-lg font-semibold tracking-tight">Patient Portal Access</DialogTitle>
          </div>
          <DialogDescription className="text-xs text-muted-foreground">
            Sign in to track symptom progression for doctor visits and attach emergency contacts to urgent triage alerts.
          </DialogDescription>
        </DialogHeader>

        <div className="px-6 pb-6">
          <Tabs value={tab} onValueChange={setTab} className="w-full">
            <TabsList className="grid grid-cols-2 w-full mb-4">
              <TabsTrigger value="login" className="text-xs">
                Sign In
              </TabsTrigger>
              <TabsTrigger value="register" className="text-xs">
                Register New Patient
              </TabsTrigger>
            </TabsList>

            {error && (
              <Alert variant="destructive" className="mb-4 py-2 text-xs flex items-center gap-2">
                <AlertCircle className="size-4 shrink-0" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {/* Login Tab */}
            <TabsContent value="login">
              <form onSubmit={handleLogin} className="flex flex-col gap-3.5">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-foreground">Email Address</label>
                  <Input
                    type="email"
                    required
                    placeholder="patient@example.com"
                    value={loginEmail}
                    onChange={(e) => setLoginEmail(e.target.value)}
                    className="h-9 text-xs"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-foreground">Password</label>
                  <Input
                    type="password"
                    required
                    placeholder="••••••••"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    className="h-9 text-xs"
                  />
                </div>
                <Button type="submit" disabled={loading} className="w-full mt-2 h-9 text-xs">
                  {loading ? <Loader2 className="size-4 animate-spin mr-2" /> : null}
                  Sign In to Patient Portal
                </Button>
              </form>
            </TabsContent>

            {/* Register Tab */}
            <TabsContent value="register">
              <form onSubmit={handleRegister} className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-2.5">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-foreground">Full Name *</label>
                    <Input
                      type="text"
                      required
                      placeholder="e.g. Ramesh Kumar"
                      value={regName}
                      onChange={(e) => setRegName(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-foreground">Email *</label>
                    <Input
                      type="email"
                      required
                      placeholder="ramesh@example.com"
                      value={regEmail}
                      onChange={(e) => setRegEmail(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-foreground">Password (min 6) *</label>
                    <Input
                      type="password"
                      required
                      minLength={6}
                      placeholder="••••••••"
                      value={regPassword}
                      onChange={(e) => setRegPassword(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-foreground">Phone *</label>
                    <Input
                      type="tel"
                      required
                      placeholder="+91 98765 43210"
                      value={regPhone}
                      onChange={(e) => setRegPhone(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-foreground">Age</label>
                    <Input
                      type="number"
                      min={0}
                      max={125}
                      placeholder="e.g. 45"
                      value={regAge}
                      onChange={(e) => setRegAge(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-foreground">Gender</label>
                    <select
                      value={regGender}
                      onChange={(e) => setRegGender(e.target.value)}
                      className="h-8 rounded-md border border-input bg-transparent px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="" className="bg-popover text-foreground">Select...</option>
                      <option value="Male" className="bg-popover text-foreground">Male</option>
                      <option value="Female" className="bg-popover text-foreground">Female</option>
                      <option value="Other" className="bg-popover text-foreground">Other</option>
                    </select>
                  </div>
                </div>

                {/* Emergency Contact Box */}
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 flex flex-col gap-2 mt-1">
                  <div className="flex items-center gap-1.5 text-destructive text-xs font-semibold">
                    <ShieldAlert className="size-3.5" />
                    <span>Emergency Contact (for urgent alerts)</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Input
                      type="text"
                      placeholder="Contact & Relation (e.g. Sunita, Wife)"
                      value={regEmergencyContact}
                      onChange={(e) => setRegEmergencyContact(e.target.value)}
                      className="h-8 text-[11px] bg-background"
                    />
                    <Input
                      type="tel"
                      placeholder="Emergency Phone (+91 98...)"
                      value={regEmergencyPhone}
                      onChange={(e) => setRegEmergencyPhone(e.target.value)}
                      className="h-8 text-[11px] bg-background"
                    />
                  </div>
                </div>

                <Button type="submit" disabled={loading} className="w-full mt-2 h-9 text-xs">
                  {loading ? <Loader2 className="size-4 animate-spin mr-2" /> : null}
                  Create Patient Profile & Sign In
                </Button>
              </form>
            </TabsContent>
          </Tabs>
        </div>
      </DialogContent>
    </Dialog>
  );
}
