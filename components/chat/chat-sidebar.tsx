"use client"

import { Plus, MessageSquare, Archive, Settings, User, LogOut, BarChart3, Server, Globe, Briefcase, FileText, ChevronDown, ChevronRight, Folder, FolderOpen } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { useState } from "react"

interface Project {
  id: string
  name: string
  count: number
  workflows: Array<{
    id: string
    name: string
    icon: React.ComponentType<any>
    status: "active" | "paused" | "error"
  }>
}

interface ChatSidebarProps {
  currentChatId?: string
  onNewChat?: () => void
  onSelectChat?: (chatId: string) => void
  onNavigate?: (route: string) => void
  className?: string
}

const recentChats = [
  { id: "1", title: "Daily Reports", icon: FileText, timestamp: "2 hours ago" },
  { id: "2", title: "Data Backup", icon: Server, timestamp: "Yesterday" },
  { id: "3", title: "Site Monitor", icon: Globe, timestamp: "3 days ago" },
  { id: "4", title: "Lead Nurture", icon: Briefcase, timestamp: "1 week ago" },
  { id: "5", title: "Sales Reports", icon: BarChart3, timestamp: "2 weeks ago" }
]

const projects: Project[] = [
  {
    id: "marketing",
    name: "Marketing",
    count: 3,
    workflows: [
      { id: "email-camp", name: "Email Campaign", icon: FileText, status: "active" },
      { id: "social-auto", name: "Social Automation", icon: Globe, status: "active" },
      { id: "lead-score", name: "Lead Scoring", icon: Briefcase, status: "paused" }
    ]
  },
  {
    id: "development",
    name: "Development",
    count: 5,
    workflows: [
      { id: "ci-cd", name: "CI/CD Pipeline", icon: Server, status: "active" },
      { id: "testing", name: "Auto Testing", icon: BarChart3, status: "active" },
      { id: "deploy", name: "Deployment", icon: Globe, status: "error" },
      { id: "monitor", name: "Monitoring", icon: FileText, status: "active" },
      { id: "backup", name: "Code Backup", icon: Server, status: "active" }
    ]
  },
  {
    id: "operations",
    name: "Operations",
    count: 2,
    workflows: [
      { id: "reports", name: "Daily Reports", icon: BarChart3, status: "active" },
      { id: "alerts", name: "System Alerts", icon: Briefcase, status: "active" }
    ]
  }
]

export function ChatSidebar({ currentChatId, onNewChat, onSelectChat, onNavigate, className }: ChatSidebarProps) {
  const [expandedProjects, setExpandedProjects] = useState<string[]>(["marketing"])

  const toggleProject = (projectId: string) => {
    setExpandedProjects(prev =>
      prev.includes(projectId)
        ? prev.filter(id => id !== projectId)
        : [...prev, projectId]
    )
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active": return "bg-green-500"
      case "paused": return "bg-yellow-500"
      case "error": return "bg-red-500"
      default: return "bg-gray-500"
    }
  }

  return (
    <div className={cn("w-[280px] bg-card border-r flex flex-col h-full", className)}>
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
                  "w-full flex items-center justify-between px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors group",
                  currentChatId === chat.id && "bg-accent"
                )}
              >
                <div className="flex items-center space-x-3 min-w-0">
                  <chat.icon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="text-sm truncate">{chat.title}</div>
                    <div className="text-xs text-muted-foreground">{chat.timestamp}</div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Projects Section */}
        <div className="px-4 pb-4">
          <Separator className="mb-3" />
          <div className="flex items-center space-x-2 mb-3">
            <Folder className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium text-muted-foreground">Projects</span>
          </div>

          <div className="space-y-1">
            {projects.map((project) => {
              const isExpanded = expandedProjects.includes(project.id)
              return (
                <div key={project.id}>
                  {/* Project Header */}
                  <button
                    onClick={() => toggleProject(project.id)}
                    className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors"
                  >
                    <div className="flex items-center space-x-2">
                      {isExpanded ? (
                        <ChevronDown className="h-3 w-3 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="h-3 w-3 text-muted-foreground" />
                      )}
                      {isExpanded ? (
                        <FolderOpen className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <Folder className="h-4 w-4 text-muted-foreground" />
                      )}
                      <span className="text-sm font-medium">{project.name}</span>
                    </div>
                    <Badge variant="secondary" className="text-xs">
                      {project.count}
                    </Badge>
                  </button>

                  {/* Project Workflows */}
                  {isExpanded && (
                    <div className="ml-6 mt-1 space-y-1">
                      {project.workflows.map((workflow) => (
                        <button
                          key={workflow.id}
                          onClick={() => onSelectChat?.(workflow.id)}
                          className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors"
                        >
                          <div className="flex items-center space-x-2 min-w-0">
                            <workflow.icon className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                            <span className="text-xs truncate">{workflow.name}</span>
                          </div>
                          <div className={cn("w-2 h-2 rounded-full", getStatusColor(workflow.status))} />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Archive */}
        <div className="px-4 pb-4">
          <Separator className="mb-3" />
          <button className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors">
            <div className="flex items-center space-x-3">
              <Archive className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm text-muted-foreground">Archive</span>
            </div>
            <Badge variant="outline" className="text-xs">12</Badge>
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="p-4 border-t space-y-1">
        <button
          onClick={() => onNavigate?.('/settings')}
          className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors"
        >
          <Settings className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">Settings</span>
        </button>
        <button
          onClick={() => onNavigate?.('/profile')}
          className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors"
        >
          <User className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">Profile</span>
        </button>
        <button
          onClick={() => onNavigate?.('/dashboard')}
          className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors"
        >
          <BarChart3 className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm">Dashboard</span>
        </button>
        <Separator className="my-2" />
        <button className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left hover:bg-accent transition-colors text-red-600">
          <LogOut className="h-4 w-4" />
          <span className="text-sm">Sign Out</span>
        </button>
      </div>
    </div>
  )
}
