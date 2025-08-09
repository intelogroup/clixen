"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { 
  Plus, 
  Home, 
  MessageSquare, 
  BarChart3, 
  Settings, 
  User, 
  LogOut,
  ChevronDown,
  ChevronRight,
  Zap
} from "lucide-react"

interface Workspace {
  id: string
  name: string
  count: number
  workflows: Array<{
    id: string
    name: string
    status: "active" | "paused" | "draft"
  }>
  expanded?: boolean
}

interface DashboardSidebarProps {
  className?: string
  onNavigate?: (route: string) => void
  onSignOut?: () => void
}

const workspaces: Workspace[] = [
  {
    id: "personal",
    name: "Personal",
    count: 4,
    expanded: true,
    workflows: [
      { id: "email-reports", name: "Email Reports", status: "active" },
      { id: "weather-alert", name: "Weather Alert", status: "active" },
      { id: "data-backup", name: "Data Backup", status: "paused" },
      { id: "news-digest", name: "News Digest", status: "draft" }
    ]
  },
  {
    id: "business",
    name: "Business",
    count: 8,
    expanded: true,
    workflows: [
      { id: "lead-capture", name: "Lead Capture", status: "active" },
      { id: "crm-sync", name: "CRM Sync", status: "active" },
      { id: "invoicing", name: "Invoicing", status: "active" },
      { id: "team-reports", name: "Team Reports", status: "paused" },
      { id: "social-posts", name: "Social Posts", status: "draft" },
      { id: "survey-auto", name: "Survey Auto", status: "draft" },
      { id: "support-tix", name: "Support Tix", status: "draft" },
      { id: "inventory", name: "Inventory", status: "draft" }
    ]
  },
  {
    id: "archive",
    name: "Archive",
    count: 12,
    expanded: false,
    workflows: []
  }
]

export function DashboardSidebar({ className, onNavigate, onSignOut }: DashboardSidebarProps) {
  const router = useRouter()
  const [workspaceStates, setWorkspaceStates] = useState<Record<string, boolean>>(
    workspaces.reduce((acc, workspace) => {
      acc[workspace.id] = workspace.expanded || false
      return acc
    }, {} as Record<string, boolean>)
  )

  const toggleWorkspace = (workspaceId: string) => {
    setWorkspaceStates(prev => ({
      ...prev,
      [workspaceId]: !prev[workspaceId]
    }))
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "active":
        return "●"
      case "paused":
        return "◐"
      case "draft":
        return "○"
      default:
        return "○"
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active":
        return "text-emerald-600"
      case "paused":
        return "text-amber-600"
      case "draft":
        return "text-slate-400"
      default:
        return "text-slate-400"
    }
  }

  const handleNavigation = (route: string) => {
    if (onNavigate) {
      onNavigate(route)
    } else {
      router.push(route)
    }
  }

  return (
    <div className={cn("w-[300px] bg-white border-r border-slate-200 flex flex-col h-full", className)}>
      {/* Header */}
      <div className="p-6 border-b border-slate-200">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-gradient-to-br from-blue-500 to-purple-600 rounded-xl">
              <Zap className="h-5 w-5 text-white" />
            </div>
            <h1 className="text-xl font-bold text-slate-900">Clixen</h1>
          </div>
        </div>

        <Button
          onClick={() => handleNavigation('/chat')}
          className="w-full justify-start bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white border-0 rounded-xl py-3 font-medium shadow-lg shadow-blue-500/25"
        >
          <Plus className="h-4 w-4 mr-2" />
          New Chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Navigation */}
        <div className="p-6">
          <div className="space-y-2">
            <Button
              variant="ghost"
              className="w-full justify-start text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-xl py-3 transition-all duration-200"
              onClick={() => handleNavigation('/dashboard')}
            >
              <Home className="h-4 w-4 mr-3" />
              Dashboard
            </Button>
            <Button
              variant="ghost"
              className="w-full justify-start text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-xl py-3 transition-all duration-200"
              onClick={() => handleNavigation('/chat')}
            >
              <MessageSquare className="h-4 w-4 mr-3" />
              Chat
            </Button>
            <Button
              variant="ghost"
              className="w-full justify-start text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-xl py-3 transition-all duration-200"
              onClick={() => handleNavigation('/analytics')}
            >
              <BarChart3 className="h-4 w-4 mr-3" />
              Analytics
            </Button>
            <Button
              variant="ghost"
              className="w-full justify-start text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-xl py-3 transition-all duration-200"
              onClick={() => handleNavigation('/settings')}
            >
              <Settings className="h-4 w-4 mr-3" />
              Settings
            </Button>
          </div>
        </div>

        <Separator className="bg-slate-200" />

        {/* Workspaces */}
        <div className="p-6">
          <h3 className="text-sm font-semibold text-slate-500 mb-4 uppercase tracking-wider">Workspaces</h3>

          <div className="space-y-3">
            {workspaces.map((workspace) => {
              const isExpanded = workspaceStates[workspace.id]

              return (
                <div key={workspace.id}>
                  {/* Workspace Header */}
                  <button
                    onClick={() => toggleWorkspace(workspace.id)}
                    className="w-full flex items-center justify-between p-3 rounded-xl hover:bg-slate-100 transition-all duration-200 group"
                  >
                    <div className="flex items-center space-x-3">
                      {isExpanded ? (
                        <ChevronDown className="h-4 w-4 text-slate-500 group-hover:text-slate-700 transition-colors" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-slate-500 group-hover:text-slate-700 transition-colors" />
                      )}
                      <span className="text-sm font-medium text-slate-700 group-hover:text-slate-900 transition-colors">{workspace.name}</span>
                    </div>
                    <Badge variant="secondary" className="bg-slate-100 text-slate-600 border-slate-200 text-xs px-2 py-1">
                      {workspace.count}
                    </Badge>
                  </button>

                  {/* Workspace Workflows */}
                  {isExpanded && workspace.workflows.length > 0 && (
                    <div className="ml-6 mt-2 space-y-1">
                      {workspace.workflows.map((workflow) => (
                        <button
                          key={workflow.id}
                          className="w-full flex items-center space-x-3 p-2 rounded-lg text-left hover:bg-slate-50 transition-all duration-200 group"
                        >
                          <span className={cn("text-sm transition-all duration-200", getStatusColor(workflow.status))}>
                            {getStatusIcon(workflow.status)}
                          </span>
                          <span className="text-sm truncate text-slate-600 group-hover:text-slate-800 transition-colors">{workflow.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="p-6 border-t border-slate-200 space-y-2">
        <Button
          variant="ghost"
          className="w-full justify-start text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-xl py-3 transition-all duration-200"
          onClick={() => handleNavigation('/profile')}
        >
          <User className="h-4 w-4 mr-3" />
          Profile
        </Button>
        <Button
          variant="ghost"
          className="w-full justify-start text-red-600 hover:text-red-700 hover:bg-red-50 rounded-xl py-3 transition-all duration-200"
          onClick={onSignOut}
        >
          <LogOut className="h-4 w-4 mr-3" />
          Sign Out
        </Button>
      </div>
    </div>
  )
}
