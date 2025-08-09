"use client"

import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { MoreVertical, Play, Pause, Settings, Archive, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface WorkflowCardDetailedProps {
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
  onAction?: (action: string, workflowId: string) => void
}

export function WorkflowCardDetailed({ 
  id, 
  name, 
  description, 
  status, 
  trigger, 
  metrics, 
  created,
  onAction 
}: WorkflowCardDetailedProps) {
  const getStatusIcon = (status: string) => {
    switch (status) {
      case "active":
        return "●"
      case "paused":
        return "◐"
      case "draft":
        return "○"
      case "error":
        return "●"
      default:
        return "○"
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active":
        return "text-green-500"
      case "paused":
        return "text-yellow-500"
      case "draft":
        return "text-gray-400"
      case "error":
        return "text-red-500"
      default:
        return "text-gray-400"
    }
  }

  const getStatusBadgeVariant = (status: string) => {
    switch (status) {
      case "active":
        return "default"
      case "paused":
        return "secondary"
      case "draft":
        return "outline"
      case "error":
        return "destructive"
      default:
        return "outline"
    }
  }

  const formatTriggerInfo = () => {
    if (status === "draft") {
      return `Status: Draft${created ? ` • Created: ${created}` : ''}`
    }
    
    if (trigger.schedule) {
      return `${trigger.schedule}${trigger.nextRun ? ` • Next: ${trigger.nextRun}` : ''}`
    }
    
    if (trigger.lastTriggered) {
      return `${trigger.type} • Last triggered: ${trigger.lastTriggered}`
    }
    
    return trigger.type
  }

  const handleAction = (action: string) => {
    onAction?.(action, id)
  }

  return (
    <Card className="group hover:shadow-md transition-shadow">
      <CardContent className="p-6">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center space-x-2">
            <span className={cn("text-lg", getStatusColor(status))}>
              {getStatusIcon(status)}
            </span>
            <h3 className="text-lg font-semibold text-gray-900">{name}</h3>
          </div>
          
          <div className="flex items-center space-x-2">
            {status === "draft" && (
              <Button size="sm" onClick={() => handleAction('deploy')}>
                Deploy Workflow
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="opacity-0 group-hover:opacity-100 transition-opacity">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {status === "active" ? (
                  <DropdownMenuItem onClick={() => handleAction('pause')}>
                    <Pause className="h-4 w-4 mr-2" />
                    Pause Workflow
                  </DropdownMenuItem>
                ) : status === "paused" ? (
                  <DropdownMenuItem onClick={() => handleAction('resume')}>
                    <Play className="h-4 w-4 mr-2" />
                    Resume Workflow
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onClick={() => handleAction('edit')}>
                  <Settings className="h-4 w-4 mr-2" />
                  Edit Settings
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => handleAction('archive')}>
                  <Archive className="h-4 w-4 mr-2" />
                  Archive
                </DropdownMenuItem>
                <DropdownMenuItem 
                  onClick={() => handleAction('delete')}
                  className="text-red-600"
                >
                  <Trash2 className="h-4 w-4 mr-2" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <p className="text-gray-600 mb-3">{description}</p>
        
        <div className="text-sm text-gray-500 mb-4">
          {formatTriggerInfo()}
          {status === "paused" && " • Status: Paused"}
        </div>

        {status === "draft" ? (
          <div className="text-sm text-gray-500">
            Ready for deployment
          </div>
        ) : (
          <div className="space-y-2">
            {/* Progress Bar */}
            <div className="flex items-center space-x-3">
              <Progress 
                value={metrics.successRate} 
                className="flex-1 h-2"
              />
              <span className="text-sm text-gray-600 min-w-0">
                {metrics.successfulRuns} successful runs
              </span>
            </div>
            
            {/* Metrics */}
            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>{metrics.totalRuns} runs</span>
              {metrics.failedRuns > 0 && (
                <span className="text-red-600">• {metrics.failedRuns} failures</span>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
