"use client";

import React from "react";
import { UserProfile } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Activity,
  LogIn,
  LogOut,
  Moon,
  ShieldCheck,
  Sun,
  ExternalLink,
} from "lucide-react";

interface HeaderProps {
  hospitalName: string;
  portalUrl?: string;
  user: UserProfile | null;
  onOpenAuth: () => void;
  onLogout: () => void;
  theme: "dark" | "light";
  onToggleTheme: () => void;
}

export function Header({
  hospitalName,
  portalUrl,
  user,
  onOpenAuth,
  onLogout,
  theme,
  onToggleTheme,
}: HeaderProps) {
  return (
    <header className="shrink-0 z-40 w-full border-b border-border/40 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground font-black text-lg tracking-tighter shadow-sm">
            m<span className="text-emerald-400 font-bold ml-0.5">+</span>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-base tracking-tight">MediAgent</span>
              <Badge variant="outline" className="text-[10px] py-0 px-1.5 h-4 text-emerald-500 border-emerald-500/30">
                Verified AI
              </Badge>
            </div>
            <div className="text-[11px] text-muted-foreground flex items-center gap-1.5">
              <span className="max-w-32 truncate sm:max-w-none">{hospitalName} Assistant</span>
              <span className="inline-block size-1 rounded-full bg-border" />
              <span className="hidden md:flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400">
                <ShieldCheck className="size-3" /> Clinical Triage Active
              </span>
            </div>
          </div>
        </div>

        {/* Right Actions */}
        <div className="flex items-center gap-2 sm:gap-3">
          {portalUrl && (
            <a
              href={portalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="hidden md:inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors px-2.5 py-1.5 rounded-lg hover:bg-muted"
            >
              Hospital Portal
              <ExternalLink className="size-3" />
            </a>
          )}

          <a
            href="/staff"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden sm:inline-flex items-center gap-1.5 text-xs font-medium border border-border/80 px-2.5 py-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            <Activity className="size-3 text-emerald-500" />
            Staff Triage ↗
          </a>

          {/* User Auth Profile */}
          {user ? (
            <div className="flex items-center gap-2 pl-1 border-l border-border/60">
              <div className="hidden lg:flex flex-col text-right">
                <span className="text-xs font-medium truncate max-w-[120px]">{user.name}</span>
                <span className="text-[10px] text-muted-foreground truncate max-w-[120px]">
                  {user.emergency_contact ? "Emergency contact set" : "Profile active"}
                </span>
              </div>
              <Avatar className="size-8 border border-border">
                <AvatarFallback className="text-xs bg-primary/10 text-primary font-semibold">
                  {user.name.charAt(0).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <Button
                variant="ghost"
                size="icon"
                onClick={onLogout}
                title="Sign out"
                aria-label="Sign out"
                className="size-8 text-muted-foreground hover:text-destructive"
              >
                <LogOut className="size-4" />
              </Button>
            </div>
          ) : (
            <Button
              variant="default"
              size="sm"
              onClick={onOpenAuth}
              className="gap-1.5 bg-primary text-primary-foreground hover:opacity-90 shadow-sm"
            >
              <LogIn className="size-3.5" />
              <span>Sign In<span className="hidden sm:inline"> / Register</span></span>
            </Button>
          )}

          {/* Theme Switcher */}
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleTheme}
            aria-label="Toggle theme"
            className="size-8 text-muted-foreground"
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </Button>
        </div>
      </div>
    </header>
  );
}
