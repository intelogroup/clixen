"use client"

import { Home, MessageSquare, Settings, User, Bell } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface BillingHeaderProps {
  activeTab?: string
}

export function BillingHeader({ activeTab = "settings" }: BillingHeaderProps) {
  const navItems = [
    { id: "dashboard", label: "Dashboard", icon: Home, href: "/dashboard" },
    { id: "chat", label: "Chat", icon: MessageSquare, href: "/chat" },
    { id: "settings", label: "Settings", icon: Settings, href: "/settings" },
    { id: "profile", label: "Profile", icon: User, href: "/profile" }
  ]

  return (
    <header className="border-b bg-white px-6 py-4">
      <div className="flex items-center justify-between">
        <nav className="flex items-center space-x-6">
          {navItems.map((item) => (
            <button
              key={item.id}
              className={cn(
                "flex items-center space-x-2 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                activeTab === item.id 
                  ? "bg-primary text-primary-foreground" 
                  : "text-muted-foreground hover:text-foreground hover:bg-accent"
              )}
            >
              <item.icon className="h-4 w-4" />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        
        <div className="flex items-center space-x-4">
          <Button variant="ghost" size="sm" className="p-2">
            <Bell className="h-4 w-4" />
          </Button>
          <span className="text-sm text-muted-foreground">john@doe.com</span>
        </div>
      </div>
    </header>
  )
}
