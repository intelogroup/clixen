"use client"

import { Plus, Mail, Server, Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

interface EmptyStateProps {
  onCreateWorkflow?: () => void
}

export function EmptyState({ onCreateWorkflow }: EmptyStateProps) {
  const examples = [
    {
      icon: Mail,
      title: "Send daily email reports",
      description: "Automated reporting to your inbox"
    },
    {
      icon: Server,
      title: "Backup files to cloud",
      description: "Scheduled data protection"
    },
    {
      icon: Clock,
      title: "Monitor website uptime",
      description: "Real-time performance alerts"
    }
  ]
  
  return (
    <Card className="mx-auto max-w-2xl">
      <CardContent className="p-12 text-center">
        <div className="mb-6">
          <div className="mx-auto w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mb-4">
            <Plus className="h-8 w-8 text-gray-400" />
          </div>
          <h3 className="text-xl font-semibold mb-2">Create your first workflow</h3>
          <p className="text-muted-foreground mb-6">
            Transform your ideas into automated workflows using natural language. 
            Just describe what you want to automate and let AI build it for you.
          </p>
        </div>
        
        <Button size="lg" onClick={onCreateWorkflow} className="mb-8">
          <Plus className="h-4 w-4 mr-2" />
          Create Your First Workflow
        </Button>
        
        <div className="space-y-3">
          <p className="text-sm font-medium text-muted-foreground mb-4">Examples:</p>
          {examples.map((example, index) => (
            <div key={index} className="flex items-center space-x-3 text-left bg-gray-50 rounded-lg p-3">
              <example.icon className="h-5 w-5 text-blue-600 flex-shrink-0" />
              <div>
                <p className="text-sm font-medium">{example.title}</p>
                <p className="text-xs text-muted-foreground">{example.description}</p>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
