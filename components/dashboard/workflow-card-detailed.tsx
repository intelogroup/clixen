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
        return "text-emerald-600"
      case "paused":
        return "text-amber-600"
      case "draft":
        return "text-slate-400"
      case "error":
        return "text-rose-600"
      default:
        return "text-slate-400"
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
    <Card className="group hover:shadow-lg hover:shadow-slate-200/50 transition-all duration-200 border-0 bg-white/70 backdrop-blur-sm hover:bg-white/90 rounded-xl overflow-hidden">
      <CardContent className="p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center space-x-2">
            <span className={cn("text-sm font-bold", getStatusColor(status))}>
              {getStatusIcon(status)}
            </span>
            <h3 className="text-sm font-semibold text-slate-900 truncate">{name}</h3>
          </div>

          <div className="flex items-center space-x-1">
            {status === "draft" && (
              <Button
                size="sm"
                onClick={() => handleAction('deploy')}
                className="bg-blue-600 hover:bg-blue-700 text-white border-0 rounded-lg px-2 py-1 text-xs font-medium"
              >
                Deploy
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="opacity-0 group-hover:opacity-100 transition-opacity h-6 w-6 p-0 rounded-md">
                  <MoreVertical className="h-3 w-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48 rounded-xl border-slate-200 shadow-xl">
                {status === "active" ? (
                  <DropdownMenuItem onClick={() => handleAction('pause')} className="rounded-lg">
                    <Pause className="h-4 w-4 mr-2" />
                    Pause Workflow
                  </DropdownMenuItem>
                ) : status === "paused" ? (
                  <DropdownMenuItem onClick={() => handleAction('resume')} className="rounded-lg">
                    <Play className="h-4 w-4 mr-2" />
                    Resume Workflow
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onClick={() => handleAction('edit')} className="rounded-lg">
                  <Settings className="h-4 w-4 mr-2" />
                  Edit Settings
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => handleAction('archive')} className="rounded-lg">
                  <Archive className="h-4 w-4 mr-2" />
                  Archive
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleAction('delete')}
                  className="text-red-600 rounded-lg"
                >
                  <Trash2 className="h-4 w-4 mr-2" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <p className="text-slate-600 mb-2 text-xs truncate">{description}</p>

        <div className="text-xs text-slate-500 mb-3 bg-slate-50 rounded-lg p-2 border border-slate-100">
          {formatTriggerInfo()}
          {status === "paused" && " • Status: Paused"}
        </div>

        {status === "draft" ? (
          <div className="text-xs text-slate-500 bg-blue-50 rounded-lg p-2 border border-blue-100">
            <span className="font-medium text-blue-700">Ready</span>
          </div>
        ) : (
          <div className="space-y-2">
            {/* Progress Bar */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-600">{metrics.successRate}% success</span>
              <span className="text-xs text-slate-500">{metrics.totalRuns} runs</span>
            </div>
            <Progress
              value={metrics.successRate}
              className="h-1.5 bg-slate-100 rounded-full overflow-hidden"
            />

            {/* Compact Metrics */}
            {metrics.failedRuns > 0 && (
              <div className="text-xs text-red-600">{metrics.failedRuns} failures</div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
