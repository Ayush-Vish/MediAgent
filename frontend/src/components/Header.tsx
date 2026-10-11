"use client";

import React from "react";
import { UserProfile } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  ExternalLink,
  LogOut,
  Moon,
  Sun,
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
    <header className="shrink-0 z-40 w-full border-b border-border/40 bg-background/60 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-4 sm:px-6">
        {/* Brand */}
        <div className="flex items-center gap-2 text-sm">
          <span className="font-semibold tracking-tight text-foreground">MediAgent</span>
          <span className="text-muted-foreground/40 font-light">/</span>
          <span className="text-xs text-muted-foreground truncate max-w-36 sm:max-w-xs font-normal">
            {hospitalName}
          </span>
        </div>

        {/* Right Actions */}
        <div className="flex items-center gap-3">
          {portalUrl && (
            <a
              href={portalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="hidden sm:inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Portal
              <ExternalLink className="size-3 opacity-60" />
            </a>
          )}

          <a
            href="/staff"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden sm:inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Staff
          </a>

          {/* User Auth Profile */}
          {user ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground hidden md:inline truncate max-w-[120px]">
                {user.name}
              </span>
              <Avatar className="size-7 border border-border/60">
                <AvatarFallback className="text-[11px] bg-muted text-foreground font-medium">
                  {user.name.charAt(0).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <Button
                variant="ghost"
                size="icon"
                onClick={onLogout}
                title="Sign out"
                aria-label="Sign out"
                className="size-7 text-muted-foreground hover:text-foreground"
              >
                <LogOut className="size-3.5" />
              </Button>
            </div>
          ) : (
            <button
              type="button"
              onClick={onOpenAuth}
              className="text-xs font-medium text-muted-foreground hover:text-foreground transition-colors px-2 py-1"
            >
              Sign In
            </button>
          )}

          {/* Theme Switcher */}
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleTheme}
            aria-label="Toggle theme"
            className="size-7 text-muted-foreground hover:text-foreground"
          >
            {theme === "dark" ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
          </Button>
        </div>
      </div>
    </header>
  );
}
