"use client"

import { Plus, Mail, Server, Clock, Rocket, BarChart3, Database, MessageSquare, Shield, Users } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface EmptyStateProps {
  onCreateWorkflow?: () => void
}

export function EmptyState({ onCreateWorkflow }: EmptyStateProps) {
  const popularAutomations = [
    "Daily reports and notifications",
    "Data backup and synchronization", 
    "Customer communication flows",
    "Social media management"
  ]

  const quickTemplates = [
    { icon: Mail, label: "Email", color: "bg-blue-100 text-blue-600" },
    { icon: Database, label: "Backup", color: "bg-green-100 text-green-600" },
    { icon: BarChart3, label: "Reports", color: "bg-purple-100 text-purple-600" },
    { icon: MessageSquare, label: "Social", color: "bg-pink-100 text-pink-600" },
    { icon: Users, label: "CRM", color: "bg-orange-100 text-orange-600" },
    { icon: Shield, label: "Alerts", color: "bg-red-100 text-red-600" }
  ]
  
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <Card className="max-w-2xl w-full bg-white/80 backdrop-blur-sm border-slate-200 shadow-xl rounded-2xl">
        <CardContent className="p-12 text-center">
          {/* Hero Icon */}
          <div className="mb-8">
            <div className="mx-auto w-20 h-20 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center mb-6 shadow-lg shadow-blue-500/25">
              <Rocket className="h-10 w-10 text-white" />
            </div>
            <h2 className="text-3xl font-bold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent mb-4">
              Create Your First Workflow
            </h2>
            <p className="text-lg text-slate-600 leading-relaxed max-w-lg mx-auto">
              Describe what you want to automate in plain English and let AI build it for you
            </p>
          </div>
          
          {/* Main CTA */}
          <Button 
            size="lg" 
            onClick={onCreateWorkflow} 
            className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white border-0 rounded-xl px-8 py-4 text-lg font-semibold shadow-lg shadow-blue-500/25 hover:shadow-blue-500/40 transition-all duration-200 mb-8"
          >
            <Plus className="h-5 w-5 mr-2" />
            Start with AI Chat
          </Button>
          
          {/* Popular Automations */}
          <div className="mb-8">
            <p className="text-sm font-medium text-slate-500 mb-4">Or choose from popular automations:</p>
            <div className="space-y-2">
              {popularAutomations.map((automation, index) => (
                <div key={index} className="text-sm text-slate-600">
                  • {automation}
                </div>
              ))}
            </div>
          </div>

          {/* Quick Templates */}
          <div>
            <p className="text-sm font-medium text-slate-500 mb-4">Quick Templates</p>
            <div className="flex flex-wrap gap-3 justify-center">
              {quickTemplates.map((template, index) => (
                <Button
                  key={index}
                  variant="outline"
                  size="sm"
                  className="border-slate-200 hover:bg-slate-50 rounded-xl px-4 py-2 transition-all duration-200"
                >
                  <div className={`w-6 h-6 rounded-lg ${template.color} flex items-center justify-center mr-2`}>
                    <template.icon className="h-3 w-3" />
                  </div>
                  {template.label}
                </Button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
