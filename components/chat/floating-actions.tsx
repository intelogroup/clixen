"use client"

import { useState } from "react"
import { Plus, FileTemplate, Plug, Lightbulb, BarChart3, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface FloatingActionsProps {
  onAction?: (action: string) => void
}

export function FloatingActions({ onAction }: FloatingActionsProps) {
  const [isOpen, setIsOpen] = useState(false)

  const actions = [
    { 
      id: "new-workflow", 
      label: "New Workflow", 
      icon: Plus, 
      description: "Start a fresh automation",
      color: "text-blue-600" 
    },
    { 
      id: "templates", 
      label: "Templates", 
      icon: FileTemplate, 
      description: "Pre-built workflows",
      color: "text-green-600" 
    },
    { 
      id: "integrations", 
      label: "Integrations", 
      icon: Plug, 
      description: "Connect your apps",
      color: "text-purple-600" 
    },
    { 
      id: "ai-suggestions", 
      label: "AI Suggestions", 
      icon: Lightbulb, 
      description: "Smart recommendations",
      color: "text-yellow-600" 
    },
    { 
      id: "dashboard", 
      label: "Dashboard", 
      icon: BarChart3, 
      description: "View your workflows",
      color: "text-gray-600" 
    }
  ]

  const handleAction = (actionId: string) => {
    onAction?.(actionId)
    setIsOpen(false)
  }

  return (
    <div className="fixed bottom-6 right-6 z-50">
      {/* Action Menu */}
      {isOpen && (
        <Card className="mb-4 w-64 animate-in slide-in-from-bottom-2 duration-200">
          <CardContent className="p-3">
            <div className="space-y-1">
              {actions.map((action) => (
                <Button
                  key={action.id}
                  variant="ghost"
                  className="w-full justify-start h-auto p-3 hover:bg-accent"
                  onClick={() => handleAction(action.id)}
                >
                  <action.icon className={cn("h-4 w-4 mr-3", action.color)} />
                  <div className="text-left">
                    <div className="text-sm font-medium">{action.label}</div>
                    <div className="text-xs text-muted-foreground">{action.description}</div>
                  </div>
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Main FAB */}
      <Button
        size="lg"
        className={cn(
          "h-14 w-14 rounded-full shadow-lg transition-all duration-200",
          isOpen && "rotate-45"
        )}
        onClick={() => setIsOpen(!isOpen)}
      >
        {isOpen ? (
          <X className="h-6 w-6" />
        ) : (
          <Plus className="h-6 w-6" />
        )}
      </Button>
    </div>
  )
}
