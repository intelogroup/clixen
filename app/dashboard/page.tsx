"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "convex/react"
import { useCurrentUser } from "@/lib/auth-context"
import { api } from "@/convex/_generated/api"
import { DashboardHeader } from "@/components/dashboard/dashboard-header"
import { DashboardStats } from "@/components/dashboard/dashboard-stats"
import { WorkflowCard } from "@/components/dashboard/workflow-card"
import { EmptyState } from "@/components/dashboard/empty-state"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { 
  Plus, 
  Filter, 
  Search, 
  Clock,
  CheckCircle,
  AlertTriangle,
  Play,
  Pause,
  Settings
} from "lucide-react"

export default function DashboardPage() {
  const router = useRouter()
  const user = useCurrentUser()
  const userProfile = useQuery(api.users.getUserProfile)
  const userStats = useQuery(api.users.getUserStats)
  const workflows = useQuery(api.workflows.getUserWorkflows, {})
  const recentRuns = useQuery(api.workflows.getWorkflowRuns, { limit: 10 })
  
  const [activeTab, setActiveTab] = useState("all")
  const [searchQuery, setSearchQuery] = useState("")

  // Loading states
  if (user === undefined || userProfile === undefined || userStats === undefined) {
    return (
      <div className="min-h-screen bg-background">
        <div className="flex items-center justify-center h-64">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      </div>
    )
  }

  // Not authenticated (shouldn't happen due to middleware)
  if (user === null) {
    router.push('/auth/signin')
    return null
  }

  const filteredWorkflows = workflows?.filter(workflow => {
    const matchesSearch = workflow.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                         workflow.description?.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesTab = activeTab === "all" || workflow.status === activeTab
    return matchesSearch && matchesTab
  }) || []

  const stats = userStats || {
    totalWorkflows: 0,
    activeWorkflows: 0,
    totalRuns: 0,
    successfulRuns: 0,
    failedRuns: 0,
    plan: "free" as const
  }

  return (
    <div className="min-h-screen bg-background">
      <DashboardHeader 
        user={{
          name: userProfile?.firstName ? `${userProfile.firstName} ${userProfile.lastName || ''}`.trim() : user.email || 'User',
          email: user.email || '',
          avatar: userProfile?.avatar || undefined,
          plan: userProfile?.plan || 'free'
        }}
      />

      <main className="container mx-auto px-4 py-8">
        {/* Stats Overview */}
        <div className="mb-8">
          <DashboardStats
            totalWorkflows={stats.totalWorkflows}
            activeWorkflows={stats.activeWorkflows}
            totalRuns={stats.totalRuns}
            successRate={stats.totalRuns > 0 ? Math.round((stats.successfulRuns / stats.totalRuns) * 100) : 0}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Main Content */}
          <div className="lg:col-span-2 space-y-6">
            {/* Workflows Section */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xl font-semibold">Your Workflows</CardTitle>
                  <Button 
                    onClick={() => router.push('/chat')}
                    className="bg-blue-600 hover:bg-blue-700"
                  >
                    <Plus className="h-4 w-4 mr-2" />
                    New Workflow
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {/* Search and Filters */}
                <div className="flex flex-col sm:flex-row gap-4 mb-6">
                  <div className="relative flex-1">
                    <Search className="h-4 w-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
                    <input
                      type="text"
                      placeholder="Search workflows..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                  <Button variant="outline" size="sm">
                    <Filter className="h-4 w-4 mr-2" />
                    Filter
                  </Button>
                </div>

                {/* Status Tabs */}
                <Tabs value={activeTab} onValueChange={setActiveTab} className="mb-6">
                  <TabsList className="grid w-full grid-cols-4">
                    <TabsTrigger value="all">All ({workflows?.length || 0})</TabsTrigger>
                    <TabsTrigger value="active">
                      Active ({workflows?.filter(w => w.status === "active").length || 0})
                    </TabsTrigger>
                    <TabsTrigger value="paused">
                      Paused ({workflows?.filter(w => w.status === "paused").length || 0})
                    </TabsTrigger>
                    <TabsTrigger value="error">
                      Error ({workflows?.filter(w => w.status === "error").length || 0})
                    </TabsTrigger>
                  </TabsList>
                </Tabs>

                {/* Workflows List */}
                {filteredWorkflows.length === 0 ? (
                  searchQuery ? (
                    <div className="text-center py-8">
                      <p className="text-gray-500">No workflows found matching "{searchQuery}"</p>
                    </div>
                  ) : (
                    <EmptyState
                      title="No workflows yet"
                      description="Create your first automation workflow using natural language"
                      actionLabel="Create Workflow"
                      onAction={() => router.push('/chat')}
                    />
                  )
                ) : (
                  <div className="space-y-4">
                    {filteredWorkflows.map((workflow) => (
                      <WorkflowCard
                        key={workflow._id}
                        id={workflow._id}
                        name={workflow.name}
                        description={workflow.description || ""}
                        status={workflow.status}
                        type={workflow.type}
                        lastRun={workflow.lastRun}
                        nextRun={workflow.nextRun}
                        runCount={workflow.runCount}
                        errorCount={workflow.errorCount}
                        trigger={workflow.trigger}
                        actions={workflow.actions}
                        createdAt={workflow.createdAt}
                      />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            {/* Recent Activity */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-semibold">Recent Activity</CardTitle>
              </CardHeader>
              <CardContent>
                {!recentRuns || recentRuns.length === 0 ? (
                  <p className="text-sm text-gray-500">No recent activity</p>
                ) : (
                  <div className="space-y-3">
                    {recentRuns.slice(0, 5).map((run) => (
                      <div key={run._id} className="flex items-center space-x-3">
                        <div className={`w-2 h-2 rounded-full ${
                          run.status === "completed" ? "bg-green-500" :
                          run.status === "failed" ? "bg-red-500" :
                          "bg-yellow-500"
                        }`} />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            Workflow run {run.status}
                          </p>
                          <p className="text-xs text-gray-500">
                            {new Date(run.startedAt).toLocaleString()}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Quick Actions */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-semibold">Quick Actions</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <Button 
                  variant="outline" 
                  className="w-full justify-start"
                  onClick={() => router.push('/chat')}
                >
                  <Plus className="h-4 w-4 mr-2" />
                  Create New Workflow
                </Button>
                <Button 
                  variant="outline" 
                  className="w-full justify-start"
                  onClick={() => router.push('/chat')}
                >
                  <Search className="h-4 w-4 mr-2" />
                  Browse Templates
                </Button>
                <Button 
                  variant="outline" 
                  className="w-full justify-start"
                  onClick={() => router.push('/billing')}
                >
                  <Settings className="h-4 w-4 mr-2" />
                  Manage Billing
                </Button>
              </CardContent>
            </Card>

            {/* Plan Status */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-semibold">Plan Status</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Current Plan</span>
                    <Badge variant={stats.plan === "free" ? "secondary" : "default"}>
                      {stats.plan.charAt(0).toUpperCase() + stats.plan.slice(1)}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Workflows</span>
                    <span className="text-sm font-medium">{stats.totalWorkflows}/∞</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Total Runs</span>
                    <span className="text-sm font-medium">{stats.totalRuns}</span>
                  </div>
                  {stats.plan === "free" && (
                    <Button 
                      size="sm" 
                      className="w-full mt-3 bg-blue-600 hover:bg-blue-700"
                      onClick={() => router.push('/billing')}
                    >
                      Upgrade Plan
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </main>
    </div>
  )
}
