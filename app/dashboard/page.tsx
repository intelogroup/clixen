"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { DashboardHeader } from "@/components/dashboard/dashboard-header"
import { DashboardStats } from "@/components/dashboard/dashboard-stats"
import { WorkflowCard } from "@/components/dashboard/workflow-card"
import { EmptyState } from "@/components/dashboard/empty-state"

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
  const [searchQuery, setSearchQuery] = useState("")
  
  const filteredWorkflows = workflows.filter(workflow =>
    workflow.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    workflow.description.toLowerCase().includes(searchQuery.toLowerCase())
  )
  
  const handleCreateWorkflow = () => {
    router.push("/chat")
  }
  
  const handleStatusChange = (id: string, status: "active" | "paused") => {
    setWorkflows(prev => prev.map(w => 
      w.id === id ? { ...w, status } : w
    ))
  }
  
  const handleViewDetails = (id: string) => {
    router.push(`/workflow/${id}`)
  }

  return (
    <div className="min-h-screen bg-background">
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
              {filteredWorkflows.map((workflow) => (
                <WorkflowCard
                  key={workflow.id}
                  {...workflow}
                  onStatusChange={handleStatusChange}
                  onViewDetails={handleViewDetails}
                />
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
    </div>
  )
}
