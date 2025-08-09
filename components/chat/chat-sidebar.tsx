"use client"

import { Plus, MessageSquare, Archive, Settings, User, LogOut, BarChart3, Server, Globe, Briefcase, FileText } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

interface ChatSidebarProps {
  currentChatId?: string
  onNewChat?: () => void
  onSelectChat?: (chatId: string) => void
}

const recentChats = [
  { id: "1", title: "Daily Reports", icon: FileText },
  { id: "2", title: "Data Backup", icon: Server },
  { id: "3", title: "Site Monitor", icon: Globe },
  { id: "4", title: "Lead Nurture", icon: Briefcase },
  { id: "5", title: "Sales Reports", icon: BarChart3 }
]

export function ChatSidebar({ currentChatId, onNewChat, onSelectChat }: ChatSidebarProps) {
  return (
    <div className="w-[280px] bg-card border-r flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b">
        <div className="flex items-center space-x-2 mb-4">
          <div className="w-2 h-2 bg-blue-500 rounded-full" />
          <h1 className="text-lg font-bold">Clixen</h1>
        </div>
        
        <Button 
          onClick={onNewChat}
          className="w-full justify-start" 
          variant="outline"
        >
          <Plus className="h-4 w-4 mr-2" />
          New Chat
        </Button>
      </div>

      {/* Recent Chats */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-4">
          <div className="flex items-center space-x-2 mb-3">
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium text-muted-foreground">Recent Chats</span>
          </div>
          <Separator className="mb-3" />
          
          <div className="space-y-1">
            {recentChats.map((chat) => (
              <button
                key={chat.id}
                onClick={() => onSelectChat?.(chat.id)}
                className={cn(
                  "w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors",
                  currentChatId === chat.id && "bg-accent"
                )}
              >
                <chat.icon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                <span className="text-sm truncate">{chat.title}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="px-4 pb-4">
          <Separator className="mb-3" />
          <button className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors">
            <Archive className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm text-muted-foreground">Archive (12)</span>
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="p-4 border-t space-y-1">
        <button className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">Settings</span>
        </button>
        <button className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors">
          <User className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">Profile</span>
        </button>
        <button className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors text-red-600">
          <LogOut className="h-4 w-4" />
          <span className="text-sm">Sign Out</span>
        </button>
      </div>
    </div>
  )
}
