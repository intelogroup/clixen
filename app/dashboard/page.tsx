"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useCurrentUser, useAuthActions } from "@/lib/auth-context"
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar"
import { WorkflowCardDetailed } from "@/components/dashboard/workflow-card-detailed"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import {
  Plus,
  Search,
  BarChart3,
  User
} from "lucide-react"

// Mock workflow data
const mockWorkflows = [
  {
    id: "daily-email-report",
    name: "Daily Email Report",
    description: "Analytics summary every morning",
    status: "active" as const,
    trigger: {
      type: "Schedule",
      schedule: "Daily 9:00 AM",
      nextRun: "in 14h"
    },
    metrics: {
      totalRuns: 127,
      successfulRuns: 127,
      failedRuns: 0,
      successRate: 100
    }
  },
  {
    id: "customer-onboarding",
    name: "Customer Onboarding",
    description: "Multi-step automation for new signups",
    status: "active" as const,
    trigger: {
      type: "Webhook",
      lastTriggered: "2h ago"
    },
    metrics: {
      totalRuns: 94,
      successfulRuns: 89,
      failedRuns: 5,
      successRate: 89
    }
  },
  {
    id: "weekly-backup",
    name: "Weekly Backup",
    description: "Database backup to cloud storage",
    status: "paused" as const,
    trigger: {
      type: "Schedule",
      schedule: "Sundays 2:00 AM"
    },
    metrics: {
      totalRuns: 52,
      successfulRuns: 52,
      failedRuns: 0,
      successRate: 100
    }
  },
  {
    id: "social-media-monitor",
    name: "Social Media Monitor",
    description: "Track mentions and respond automatically",
    status: "draft" as const,
    trigger: {
      type: "Webhook"
    },
    metrics: {
      totalRuns: 0,
      successfulRuns: 0,
      failedRuns: 0,
      successRate: 0
    },
    created: "Yesterday"
  }
]

export default function DashboardPage() {
  const router = useRouter()
  const user = useCurrentUser()
  const { signOut } = useAuthActions()
  const [activeTab, setActiveTab] = useState("all")
  const [searchQuery, setSearchQuery] = useState("")

  // Filter workflows based on active tab and search
  const filteredWorkflows = mockWorkflows.filter(workflow => {
    const matchesSearch = workflow.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                         workflow.description.toLowerCase().includes(searchQuery.toLowerCase())
    
    const matchesTab = activeTab === "all" || 
                      (activeTab === "active" && workflow.status === "active") ||
                      (activeTab === "draft" && workflow.status === "draft") ||
                      (activeTab === "paused" && workflow.status === "paused")
    
    return matchesSearch && matchesTab
  })

  const getTabCount = (tab: string) => {
    if (tab === "all") return mockWorkflows.length
    return mockWorkflows.filter(w => w.status === tab).length
  }

  const handleWorkflowAction = (action: string, workflowId: string) => {
    console.log(`Action: ${action} on workflow: ${workflowId}`)
    // Handle workflow actions here
  }

  const handleSignOut = async () => {
    await signOut()
    router.push('/auth/signin')
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50 flex">
      {/* Sidebar - Always Visible */}
      <div className="relative">
        <DashboardSidebar onSignOut={handleSignOut} />
      </div>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col max-w-[900px] mx-auto w-full">
        {/* Header */}
        <div className="bg-white/80 backdrop-blur-sm border-b border-white/20 px-6 py-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <h1 className="text-2xl font-bold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent">
                Workflows
              </h1>
            </div>

            <div className="flex items-center space-x-4">
              <Button variant="outline" size="sm" className="border-slate-200 hover:bg-slate-50 rounded-xl px-4 py-2 font-medium transition-all duration-200">
                <BarChart3 className="h-4 w-4 mr-2" />
                Analytics
              </Button>

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
        <div className="flex-1 p-4">
          <div className="space-y-3">
            {filteredWorkflows.length === 0 ? (
              <div className="text-center py-8">
                <div className="text-slate-500 mb-4 text-sm">
                  {searchQuery ? `No workflows found matching "${searchQuery}"` : "No workflows found"}
                </div>
                <Button
                  onClick={() => router.push('/chat')}
                  className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white border-0 rounded-xl px-4 py-2 text-sm font-medium"
                >
                  <Plus className="h-4 w-4 mr-2" />
                  Create Workflow
                </Button>
              </div>
            ) : (
              filteredWorkflows.map((workflow) => (
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
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
