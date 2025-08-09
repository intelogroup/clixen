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
    <div className="min-h-screen bg-gray-50 flex">
      {/* Sidebar - Always Visible */}
      <div className="relative">
        <DashboardSidebar onSignOut={handleSignOut} />
      </div>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col max-w-[900px] mx-auto w-full">
        {/* Header */}
        <div className="bg-white border-b px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <h1 className="text-2xl font-bold text-gray-900">Workflows</h1>
            </div>
            
            <div className="flex items-center space-x-3">
              <Button variant="outline" size="sm">
                <BarChart3 className="h-4 w-4 mr-2" />
                Analytics
              </Button>
              
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm">
                    <Avatar className="w-6 h-6">
                      <AvatarFallback className="text-xs">
                        {user?.firstName?.[0]}{user?.lastName?.[0]}
                      </AvatarFallback>
                    </Avatar>
                    <span className="ml-2 hidden sm:inline">Profile</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => router.push('/profile')}>
                    <User className="h-4 w-4 mr-2" />
                    View Profile
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={handleSignOut} className="text-red-600">
                    Sign Out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </div>

        {/* Workflow Controls */}
        <div className="bg-white border-b px-6 py-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-4">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full sm:flex-1">
              <TabsList className="grid w-full grid-cols-4 max-w-lg">
                <TabsTrigger value="active" className="text-xs sm:text-sm">
                  Active ({getTabCount("active")})
                </TabsTrigger>
                <TabsTrigger value="draft" className="text-xs sm:text-sm">
                  Draft ({getTabCount("draft")})
                </TabsTrigger>
                <TabsTrigger value="all" className="text-xs sm:text-sm">
                  All ({getTabCount("all")})
                </TabsTrigger>
                <TabsTrigger value="paused" className="text-xs sm:text-sm">
                  Paused ({getTabCount("paused")})
                </TabsTrigger>
              </TabsList>
            </Tabs>
            
            <Button 
              onClick={() => router.push('/chat')}
              className="bg-blue-600 hover:bg-blue-700"
            >
              <Plus className="h-4 w-4 mr-2" />
              Create New
            </Button>
          </div>
          
          <div className="relative">
            <Search className="h-4 w-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
            <Input
              type="text"
              placeholder="Search workflows..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>

        {/* Workflow List */}
        <div className="flex-1 p-6">
          <div className="space-y-4">
            {filteredWorkflows.length === 0 ? (
              <div className="text-center py-12">
                <div className="text-gray-500 mb-4">
                  {searchQuery ? `No workflows found matching "${searchQuery}"` : "No workflows found"}
                </div>
                <Button 
                  onClick={() => router.push('/chat')}
                  className="bg-blue-600 hover:bg-blue-700"
                >
                  <Plus className="h-4 w-4 mr-2" />
                  Create Your First Workflow
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
