"use client";

import React, { useState, useEffect } from "react";
import { UserProfile } from "@/types/api";
import { authApi, chatApi } from "@/lib/api";
import { Header } from "@/components/Header";
import { ChatInterface, HEALTH_FACTS } from "@/components/ChatInterface";
import { AuthModal } from "@/components/AuthModal";
import { Sparkles } from "lucide-react";

function SessionFactLoader() {
  const [factIndex, setFactIndex] = useState(() => Math.floor(Math.random() * HEALTH_FACTS.length));
  const [fade, setFade] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => {
      setFade(true);
      setTimeout(() => {
        setFactIndex((prev) => (prev + 1) % HEALTH_FACTS.length);
        setFade(false);
      }, 250);
    }, 4500);
    return () => clearInterval(timer);
  }, []);

  const current = HEALTH_FACTS[factIndex] || HEALTH_FACTS[0];

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
      <div className="flex items-start gap-3 text-xs py-3 px-4.5 rounded-2xl border border-border/50 bg-muted/20 max-w-md shadow-xs">
        <Sparkles className="size-4 mt-0.5 shrink-0 text-foreground/80 animate-pulse" />
        <div
          className={`text-left leading-relaxed transition-all duration-200 ${
            fade ? "opacity-0 translate-y-1" : "opacity-100 translate-y-0"
          }`}
        >
          <span className="font-semibold text-foreground">Health Fact: </span>
          <span className="text-foreground/90 font-medium">{current.fact}</span>
          <span className="ml-1.5 text-[10px] text-muted-foreground/60 font-medium">({current.source})</span>
        </div>
      </div>
      <p className="mt-3 text-[11px] text-muted-foreground/60 font-medium">
        Connecting and initializing session…
      </p>
    </div>
  );
}

export default function Home() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [hospitalName, setHospitalName] = useState<string>("Hospital");
  const [portalUrl, setPortalUrl] = useState<string>("");
  const [authOpen, setAuthOpen] = useState<boolean>(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    // 1. Check current logged-in user
    authApi
      .me()
      .then((currentUser) => {
        setUser(currentUser);
      })
      .catch(() => {
        setUser(null);
      })
      .finally(() => {
        setAuthReady(true);
      });

    // 2. Fetch hospital configuration
    chatApi
      .getConfig()
      .then((config) => {
        if (config?.hospital) setHospitalName(config.hospital);
        if (config?.portal_url) setPortalUrl(config.portal_url);
      })
      .catch(() => {
        // Fallback default
        setHospitalName("CMC Vellore");
      });

    // 3. Theme setup
    try {
      const savedTheme = localStorage.getItem("mediagent-theme") as "dark" | "light" | null;
      if (savedTheme === "dark" || savedTheme === "light") {
        queueMicrotask(() => setTheme(savedTheme));
        document.documentElement.classList.toggle("dark", savedTheme === "dark");
      }
    } catch {
      // localStorage may be disabled
    }
  }, []);

  const handleToggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    document.documentElement.classList.toggle("dark", nextTheme === "dark");
    try {
      localStorage.setItem("mediagent-theme", nextTheme);
    } catch {
      // storage unavailable
    }
  };

  const handleLogout = async () => {
    if (confirm(`Signed in as ${user?.name}.\n\nWould you like to sign out?`)) {
      try {
        await authApi.logout();
        setUser(null);
      } catch (err) {
        console.error("Logout failed:", err);
      }
    }
  };

  return (
    <div className="h-dvh overflow-hidden flex flex-col bg-background text-foreground">
      <Header
        hospitalName={hospitalName}
        portalUrl={portalUrl}
        user={user}
        onOpenAuth={() => setAuthOpen(true)}
        onLogout={handleLogout}
        theme={theme}
        onToggleTheme={handleToggleTheme}
      />

      <main className="min-h-0 flex-1 flex flex-col">
        {authReady ? (
          <ChatInterface
            key={user?.id ?? "anonymous"}
            user={user}
            onOpenAuth={() => setAuthOpen(true)}
            hospitalName={hospitalName}
          />
        ) : (
          <SessionFactLoader />
        )}
      </main>

      <AuthModal
        open={authOpen}
        onOpenChange={setAuthOpen}
        onSuccess={(newUser) => setUser(newUser)}
      />

    </div>
  );
}
