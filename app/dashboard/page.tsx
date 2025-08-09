"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { DashboardHeader } from "@/components/dashboard/dashboard-header"
import { DashboardStats } from "@/components/dashboard/dashboard-stats"
import { WorkflowCard } from "@/components/dashboard/workflow-card"
import { EmptyState } from "@/components/dashboard/empty-state"
import { FloatingActions } from "@/components/chat/floating-actions"
import { TransitionManager, TransitionType } from "@/components/transitions/transition-manager"
import { ModalManager, ModalType } from "@/components/modals/modal-manager"

const mockWorkflows = [
  {
    id: "1",
    name: "Daily Analytics Reminder Email",
    description: "Send daily email reminders to check analytics dashboard",
    status: "active" as const,
    created: "Aug 9",
    executions: 15,
    lastRun: "Aug 8"
  },
  {
    id: "2", 
    name: "Weekly Backup Automation",
    description: "Backup files to Google Drive every Sunday",
    status: "paused" as const,
    created: "Aug 7",
    executions: 3,
    lastRun: "Aug 6"
  },
  {
    id: "3",
    name: "Slack Integration Test", 
    description: "Test workflow for Slack notifications",
    status: "failed" as const,
    created: "Aug 6",
    executions: 0,
    error: "Invalid webhook URL"
  },
  {
    id: "4",
    name: "Customer Survey Automation",
    description: "Send follow-up surveys after purchases", 
    status: "draft" as const,
    created: "Aug 5",
    executions: 0
  },
  {
    id: "5",
    name: "Website Monitor",
    description: "Monitor website uptime and send alerts",
    status: "active" as const,
    created: "Aug 4", 
    executions: 120,
    lastRun: "5 min ago"
  }
]

export default function DashboardPage() {
  const router = useRouter()
  const [workflows, setWorkflows] = useState(mockWorkflows)
  const [currentTransition, setCurrentTransition] = useState<TransitionType>(null)
  const [currentModal, setCurrentModal] = useState<ModalType>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")

  const filteredWorkflows = workflows.filter(workflow =>
    workflow.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    workflow.description.toLowerCase().includes(searchQuery.toLowerCase())
  )
  const [searchQuery, setSearchQuery] = useState("")
  
  const filteredWorkflows = workflows.filter(workflow =>
    workflow.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    workflow.description.toLowerCase().includes(searchQuery.toLowerCase())
  )
  
  const handleCreateWorkflow = () => {
    setCurrentTransition("workflow-generation")
  }

  const handleQuickAction = (type: string) => {
    switch (type) {
      case "social-media":
        setCurrentTransition("workflow-generation")
        break
      case "email-automation":
        setCurrentTransition("workflow-generation")
        break
      case "data-backup":
        setCurrentTransition("workflow-generation")
        break
      default:
        router.push("/chat")
    }
  }
  
  const handleStatusChange = (id: string, status: "active" | "paused") => {
    setWorkflows(prev => prev.map(w => {
      if (w.id === id) {
        // Only update if the workflow can have this status
        if (w.status === "active" || w.status === "paused") {
          return { ...w, status }
        }
      }
      return w
    }))
  }
  
  const handleViewDetails = (id: string) => {
    router.push(`/workflow/${id}`)
  }

  const handleFloatingAction = (action: string) => {
    switch (action) {
      case "new-workflow":
        setCurrentTransition("workflow-generation")
        break
      case "templates":
        router.push("/templates")
        break
      case "integrations":
        setCurrentModal("api-setup")
        break
      case "ai-suggestions":
        setCurrentModal("oauth-permission")
        break
      case "dashboard":
        // Already on dashboard
        break
    }
  }

  const handleTransitionComplete = (result?: any) => {
    setCurrentTransition(null)
    if (result?.workflowId) {
      // Add new workflow to list
      const newWorkflow = {
        id: result.workflowId,
        name: "New AI Generated Workflow",
        description: "Automatically created workflow",
        status: "active" as const,
        created: "Just now",
        executions: 0,
        lastRun: "Never"
      }
      setWorkflows(prev => [newWorkflow, ...prev])
    }
    router.push("/chat")
  }

  const handleModalComplete = (result?: any) => {
    setCurrentModal(null)
    if (result?.services) {
      console.log("API services configured:", result.services)
    }
  }

  return (
    <TransitionManager
      type={currentTransition}
      workflowType="AI-powered automation"
      onComplete={handleTransitionComplete}
      onCancel={() => setCurrentTransition(null)}
    >
      <ModalManager
        type={currentModal}
        onComplete={handleModalComplete}
        onCancel={() => setCurrentModal(null)}
      >
        <div className="min-h-screen bg-background relative">
      <DashboardHeader 
        workflowCount={workflows.length}
        onCreateWorkflow={handleCreateWorkflow}
        onSearch={setSearchQuery}
      />
      
      <div className="container mx-auto px-4 py-6">
        {workflows.length > 0 && (
          <div className="mb-8">
            <DashboardStats />
          </div>
        )}

        {/* Quick Actions inspired by mockups */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <button
            onClick={() => handleQuickAction("social-media")}
            className="p-4 bg-gradient-to-r from-purple-500 to-pink-500 text-white rounded-xl hover:shadow-lg transition-all duration-200 hover:scale-105 group"
          >
            <div className="flex items-center space-x-3">
              <div className="text-2xl group-hover:scale-110 transition-transform">📱</div>
              <div className="text-left">
                <div className="font-semibold">Social Media</div>
                <div className="text-sm opacity-90">Automate posting</div>
              </div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAction("email-automation")}
            className="p-4 bg-gradient-to-r from-blue-500 to-cyan-500 text-white rounded-xl hover:shadow-lg transition-all duration-200 hover:scale-105 group"
          >
            <div className="flex items-center space-x-3">
              <div className="text-2xl group-hover:scale-110 transition-transform">📧</div>
              <div className="text-left">
                <div className="font-semibold">Email Automation</div>
                <div className="text-sm opacity-90">Smart campaigns</div>
              </div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAction("data-backup")}
            className="p-4 bg-gradient-to-r from-green-500 to-emerald-500 text-white rounded-xl hover:shadow-lg transition-all duration-200 hover:scale-105 group"
          >
            <div className="flex items-center space-x-3">
              <div className="text-2xl group-hover:scale-110 transition-transform">💾</div>
              <div className="text-left">
                <div className="font-semibold">Data Backup</div>
                <div className="text-sm opacity-90">Secure storage</div>
              </div>
            </div>
          </button>
        </div>
        
        <div className="space-y-4">
          {workflows.length === 0 ? (
            <EmptyState onCreateWorkflow={handleCreateWorkflow} />
          ) : filteredWorkflows.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-muted-foreground mb-4">No workflows match "{searchQuery}"</p>
              <p className="text-sm text-muted-foreground">
                Try a different search term or create a new workflow.
              </p>
            </div>
          ) : (
            <div className="grid gap-4">
              {filteredWorkflows.map((workflow, index) => (
                <div
                  key={workflow.id}
                  className="animate-in slide-in-from-bottom-2 duration-300"
                  style={{ animationDelay: `${index * 100}ms` }}
                >
                  <WorkflowCard
                    {...workflow}
                    onStatusChange={handleStatusChange}
                    onViewDetails={handleViewDetails}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
        
        {workflows.length > 0 && (
          <div className="text-center mt-8 py-4 text-sm text-muted-foreground">
            Powered by AI • Built for automation • {workflows.length} workflow{workflows.length === 1 ? '' : 's'}
          </div>
        )}
      </div>

      {/* Floating Actions inspired by your mockups */}
      <FloatingActions onAction={handleFloatingAction} />
    </div>
    </ModalManager>
    </TransitionManager>
  )
}
