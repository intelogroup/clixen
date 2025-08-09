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
    <Card className="group hover:shadow-xl hover:shadow-slate-200/50 transition-all duration-300 border-0 bg-white/70 backdrop-blur-sm hover:bg-white/90 rounded-2xl overflow-hidden">
      <CardContent className="p-8">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center space-x-4">
            <div className={cn("p-2 rounded-xl",
              status === "active" ? "bg-green-100" :
              status === "paused" ? "bg-yellow-100" :
              status === "draft" ? "bg-slate-100" :
              "bg-red-100"
            )}>
              <span className={cn("text-xl font-bold", getStatusColor(status))}>
                {getStatusIcon(status)}
              </span>
            </div>
            <h3 className="text-xl font-bold text-slate-900">{name}</h3>
          </div>

          <div className="flex items-center space-x-3">
            {status === "draft" && (
              <Button
                size="sm"
                onClick={() => handleAction('deploy')}
                className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white border-0 rounded-xl px-4 py-2 font-semibold shadow-lg shadow-blue-500/25 transition-all duration-200"
              >
                Deploy Workflow
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="opacity-0 group-hover:opacity-100 transition-all duration-200 hover:bg-slate-100 rounded-xl">
                  <MoreVertical className="h-4 w-4" />
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

        <p className="text-slate-600 mb-4 text-lg leading-relaxed">{description}</p>

        <div className="text-sm text-slate-500 mb-6 bg-slate-50 rounded-xl p-4 border border-slate-100">
          {formatTriggerInfo()}
          {status === "paused" && " • Status: Paused"}
        </div>

        {status === "draft" ? (
          <div className="text-sm text-slate-500 bg-blue-50 rounded-xl p-4 border border-blue-100">
            <span className="font-medium text-blue-700">Ready for deployment</span>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Progress Bar */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-700">Success Rate</span>
                <span className="text-sm font-bold text-slate-900">{metrics.successRate}%</span>
              </div>
              <Progress
                value={metrics.successRate}
                className="h-3 bg-slate-100 rounded-full overflow-hidden"
              />
            </div>

            {/* Metrics */}
            <div className="grid grid-cols-3 gap-4 pt-2">
              <div className="text-center">
                <div className="text-xl font-bold text-slate-900">{metrics.totalRuns}</div>
                <div className="text-xs text-slate-500 font-medium">Total Runs</div>
              </div>
              <div className="text-center">
                <div className="text-xl font-bold text-green-600">{metrics.successfulRuns}</div>
                <div className="text-xs text-slate-500 font-medium">Successful</div>
              </div>
              <div className="text-center">
                <div className="text-xl font-bold text-red-600">{metrics.failedRuns}</div>
                <div className="text-xs text-slate-500 font-medium">Failures</div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
