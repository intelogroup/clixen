"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { useCurrentUser, useAuthActions } from "@/lib/auth-context"
import { RequireAuth } from "@/components/auth/require-auth"
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar"
import { WorkflowCardDetailed } from "@/components/dashboard/workflow-card-detailed"
import { EmptyState } from "@/components/dashboard/empty-state"
import { MobileSidebar } from "@/components/ui/mobile-sidebar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import {
  Plus,
  Search,
  User
} from "lucide-react"

interface WorkflowInstance {
  id: string
  automation_id?: string
  task_name: string
  status: "active" | "paused" | "draft" | "error"
  trigger_type?: string
  action_type?: string
  schedule?: { [key: string]: any }
  next_run_at?: string | null
  last_run_at?: string | null
  last_result?: any
  run_history?: any[]
  created_at?: string
}

interface WorkflowCardData {
  id: string
  name: string
  description: string
  status: "active" | "paused" | "draft" | "error"
  trigger: {
    type: string
    schedule?: string
    nextRun?: string
    lastTriggered?: string
  }
  metrics: {
    totalRuns: number
    successfulRuns: number
    failedRuns: number
    successRate: number
  }
  created?: string
}

function formatSchedule(schedule?: { [key: string]: any }): string {
  if (!schedule || typeof schedule !== "object") return "On demand"
  const s = schedule as any
  const freq = s.frequency || s.schedule || s.cron || ""
  const time = s.time || ""
  return [freq, time].filter(Boolean).join(" ") || "On demand"
}

function formatNextRun(next?: string | null): string | undefined {
  if (!next) return undefined
  const d = new Date(next)
  if (isNaN(d.getTime())) return undefined
  const diff = d.getTime() - Date.now()
  if (diff < 0) return "due"
  const mins = Math.round(diff / 60000)
  if (mins < 60) return `in ${mins}m`
  const hrs = Math.round(mins / 60)
  if (hrs < 48) return `in ${hrs}h`
  return `in ${Math.round(hrs / 24)}d`
}

function toCardData(w: WorkflowInstance): WorkflowCardData {
  const history = Array.isArray(w.run_history) ? w.run_history : []
  const totalRuns = history.length
  const successfulRuns = history.filter((r) => (r as any).status === "success").length
  const failedRuns = history.filter((r) => (r as any).status === "error").length
  const successRate = totalRuns ? Math.round((successfulRuns / totalRuns) * 100) : 0
  return {
    id: w.id,
    name: w.task_name || w.automation_id || w.id,
    description: w.automation_id ? `Automation: ${w.automation_id}` : "",
    status: w.status || "draft",
    trigger: {
      type: w.trigger_type === "schedule" ? "Schedule" : w.trigger_type === "email" ? "Email" : w.trigger_type || "Manual",
      schedule: w.trigger_type === "schedule" ? formatSchedule(w.schedule) : undefined,
      nextRun: formatNextRun(w.next_run_at),
      lastTriggered: w.last_run_at ? new Date(w.last_run_at).toLocaleString() : undefined,
    },
    metrics: { totalRuns, successfulRuns, failedRuns, successRate },
    created: w.created_at ? new Date(w.created_at).toLocaleDateString() : undefined,
  }
}

export default function DashboardPage() {
  const router = useRouter()
  const user = useCurrentUser()
  const { signOut } = useAuthActions()
  const [activeTab, setActiveTab] = useState("all")
  const [searchQuery, setSearchQuery] = useState("")
  const [workflows, setWorkflows] = useState<WorkflowCardData[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch("/api/workflows")
      .then(async (res) => {
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        const data = await res.json()
        if (cancelled) return
        const list = Array.isArray(data.workflows) ? data.workflows : []
        setWorkflows(list.map(toCardData))
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err.message || "Failed to load workflows")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  // Filter workflows based on active tab and search
  const filteredWorkflows = workflows.filter(workflow => {
    const matchesSearch = workflow.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                         workflow.description.toLowerCase().includes(searchQuery.toLowerCase())

    const matchesTab = activeTab === "all" ||
                      (activeTab === "active" && workflow.status === "active") ||
                      (activeTab === "draft" && workflow.status === "draft") ||
                      (activeTab === "paused" && workflow.status === "paused")

    return matchesSearch && matchesTab
  })

  const getTabCount = (tab: string) => {
    if (tab === "all") return workflows.length
    return workflows.filter(w => w.status === tab).length
  }

  const handleWorkflowAction = async (action: string, workflowId: string) => {
    if (action === "pause" || action === "resume" || action === "run-now") {
      try {
        const res = await fetch(`/api/workflows/${workflowId}?action=${action}`, { method: "POST" })
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        setWorkflows(prev =>
          prev.map(w =>
            w.id === workflowId
              ? { ...w, status: action === "pause" ? "paused" : action === "resume" ? "active" : w.status }
              : w
          )
        )
      } catch (err: any) {
        setLoadError(err.message || `Failed to ${action} workflow`)
      }
    } else if (action === "delete") {
      try {
        const res = await fetch(`/api/workflows/${workflowId}`, { method: "DELETE" })
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        setWorkflows(prev => prev.filter(w => w.id !== workflowId))
      } catch (err: any) {
        setLoadError(err.message || "Failed to delete workflow")
      }
    }
  }

  const handleSignOut = async () => {
    await signOut()
    router.push('/auth/signin')
  }

  return (
    <RequireAuth>
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50 flex">
      {/* Mobile Sidebar */}
      <MobileSidebar onSignOut={handleSignOut} />

      {/* Desktop Sidebar - Hidden on mobile */}
      <div className="hidden md:block relative">
        <DashboardSidebar
          onSignOut={handleSignOut}
          workflows={workflows.map(w => ({ id: w.id, name: w.name, status: w.status }))}
        />
      </div>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col h-screen">
        {/* Header */}
        <div className="bg-white/80 backdrop-blur-sm border-b border-white/20 px-6 py-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <h1 className="text-2xl font-bold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent">
                Workflows
              </h1>
            </div>

            <div className="flex items-center space-x-4">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className="hover:bg-slate-100 rounded-xl transition-all duration-200">
                    <Avatar className="w-8 h-8 ring-2 ring-slate-200">
                      <AvatarFallback className="text-sm font-semibold bg-gradient-to-br from-blue-500 to-purple-600 text-white">
                        {user?.firstName?.[0]}{user?.lastName?.[0]}
                      </AvatarFallback>
                    </Avatar>
                    <span className="ml-3 hidden sm:inline font-medium text-slate-700">Profile</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48 rounded-xl border-slate-200 shadow-xl">
                  <DropdownMenuItem onClick={() => router.push('/profile')} className="rounded-lg">
                    <User className="h-4 w-4 mr-2" />
                    View Profile
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={handleSignOut} className="text-red-600 rounded-lg">
                    Sign Out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </div>

        {/* Workflow Controls */}
        <div className="bg-white/80 backdrop-blur-sm border-b border-white/20 px-6 py-4 shadow-sm">
          {loadError && (
            <div className="mb-4 px-4 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
              {loadError}
            </div>
          )}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-4">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full sm:flex-1">
              <TabsList className="grid w-full grid-cols-4 max-w-lg bg-slate-100 p-1 rounded-xl">
                <TabsTrigger value="active" className="text-xs sm:text-sm rounded-lg font-medium data-[state=active]:bg-white data-[state=active]:shadow-sm transition-all duration-200">
                  Active ({getTabCount("active")})
                </TabsTrigger>
                <TabsTrigger value="draft" className="text-xs sm:text-sm rounded-lg font-medium data-[state=active]:bg-white data-[state=active]:shadow-sm transition-all duration-200">
                  Draft ({getTabCount("draft")})
                </TabsTrigger>
                <TabsTrigger value="all" className="text-xs sm:text-sm rounded-lg font-medium data-[state=active]:bg-white data-[state=active]:shadow-sm transition-all duration-200">
                  All ({getTabCount("all")})
                </TabsTrigger>
                <TabsTrigger value="paused" className="text-xs sm:text-sm rounded-lg font-medium data-[state=active]:bg-white data-[state=active]:shadow-sm transition-all duration-200">
                  Paused ({getTabCount("paused")})
                </TabsTrigger>
              </TabsList>
            </Tabs>

            <Button
              onClick={() => router.push('/chat')}
              className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white border-0 rounded-xl px-6 py-3 font-semibold shadow-lg shadow-blue-500/25 hover:shadow-blue-500/40 transition-all duration-200"
            >
              <Plus className="h-4 w-4 mr-2" />
              Create New
            </Button>
          </div>

          <div className="relative">
            <Search className="h-5 w-5 absolute left-4 top-1/2 transform -translate-y-1/2 text-slate-400" />
            <Input
              type="text"
              placeholder="Search workflows..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-12 py-3 rounded-xl border-slate-200 bg-white/50 backdrop-blur-sm focus:bg-white transition-all duration-200 placeholder:text-slate-400"
            />
          </div>
        </div>

        {/* Workflow List */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <div className="flex items-center gap-2 text-slate-500">
                <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce"></div>
                <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></div>
                <div className="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></div>
                <span className="text-sm">Loading workflows...</span>
              </div>
            </div>
          ) : filteredWorkflows.length === 0 ? (
            searchQuery ? (
              <div className="flex-1 flex items-center justify-center p-8">
                <div className="text-center">
                  <div className="text-slate-500 mb-4">
                    No workflows found matching "{searchQuery}"
                  </div>
                  <Button
                    onClick={() => router.push('/chat')}
                    className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white border-0 rounded-xl px-4 py-2 text-sm font-medium"
                  >
                    <Plus className="h-4 w-4 mr-2" />
                    Create Workflow
                  </Button>
                </div>
              </div>
            ) : (
              <EmptyState onCreateWorkflow={() => router.push('/chat')} />
            )
          ) : (
            <div className="p-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
                {filteredWorkflows.map((workflow) => (
                  <WorkflowCardDetailed
                    key={workflow.id}
                    id={workflow.id}
                    name={workflow.name}
                    description={workflow.description}
                    status={workflow.status}
                    trigger={workflow.trigger}
                    metrics={workflow.metrics}
                    created={workflow.created}
                    onAction={handleWorkflowAction}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      </div>
    </RequireAuth>
  )
}
