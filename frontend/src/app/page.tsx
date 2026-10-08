"use client";

import React, { useState, useEffect } from "react";
import { UserProfile } from "@/types/api";
import { authApi, chatApi } from "@/lib/api";
import { Header } from "@/components/Header";
import { ChatInterface } from "@/components/ChatInterface";
import { AuthModal } from "@/components/AuthModal";

export default function Home() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [hospitalName, setHospitalName] = useState<string>("Hospital");
  const [portalUrl, setPortalUrl] = useState<string>("");
  const [authOpen, setAuthOpen] = useState<boolean>(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    // 1. Check current logged-in user
    authApi.me().then((currentUser) => {
      setUser(currentUser);
    });

    // 2. Fetch hospital configuration
    chatApi.getConfig().then((config) => {
      setHospitalName(config.hospital);
      setPortalUrl(config.portal_url);
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
        <ChatInterface
          key={user?.id ?? "anonymous"}
          user={user}
          onOpenAuth={() => setAuthOpen(true)}
          hospitalName={hospitalName}
        />
      </main>

      <AuthModal
        open={authOpen}
        onOpenChange={setAuthOpen}
        onSuccess={(newUser) => setUser(newUser)}
      />

    </div>
  );
}
