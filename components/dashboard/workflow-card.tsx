"use client"

import { MoreVertical, Play, Pause, Copy, Trash2, Settings, Calendar, Clock, CheckCircle, XCircle, AlertTriangle } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

interface WorkflowCardProps {
  id: string
  name: string
  description: string
  status: "active" | "paused" | "failed" | "draft"
  created: string
  executions: number
  lastRun?: string
  error?: string
  onStatusChange?: (id: string, status: "active" | "paused") => void
  onEdit?: (id: string) => void
  onDuplicate?: (id: string) => void
  onDelete?: (id: string) => void
  onViewDetails?: (id: string) => void
}

const statusConfig = {
  active: {
    icon: CheckCircle,
    color: "bg-green-500",
    badge: "bg-green-100 text-green-800",
    text: "Active"
  },
  paused: {
    icon: Pause,
    color: "bg-blue-500", 
    badge: "bg-blue-100 text-blue-800",
    text: "Paused"
  },
  failed: {
    icon: XCircle,
    color: "bg-red-500",
    badge: "bg-red-100 text-red-800", 
    text: "Failed"
  },
  draft: {
    icon: AlertTriangle,
    color: "bg-yellow-500",
    badge: "bg-yellow-100 text-yellow-800",
    text: "Draft"
  }
}

export function WorkflowCard({
  id,
  name,
  description,
  status,
  created,
  executions,
  lastRun,
  error,
  onStatusChange,
  onEdit,
  onDuplicate,
  onDelete,
  onViewDetails
}: WorkflowCardProps) {
  const StatusIcon = statusConfig[status].icon
  
  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardContent className="p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-start space-x-3 flex-1">
            <div className="flex items-center justify-center w-10 h-10 rounded-full bg-gray-100">
              <StatusIcon className={cn("h-5 w-5", 
                status === "active" ? "text-green-600" :
                status === "paused" ? "text-blue-600" :
                status === "failed" ? "text-red-600" : 
                "text-yellow-600"
              )} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center space-x-2 mb-1">
                <h3 
                  className="font-semibold text-gray-900 truncate cursor-pointer hover:text-blue-600" 
                  onClick={() => onViewDetails?.(id)}
                >
                  {name}
                </h3>
                <Badge variant="secondary" className={statusConfig[status].badge}>
                  {statusConfig[status].text}
                </Badge>
              </div>
              <p className="text-sm text-gray-600 mb-3 line-clamp-2">{description}</p>
              
              {error && (
                <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                  {error}
                </div>
              )}
              
              <div className="flex items-center space-x-4 text-xs text-gray-500">
                <div className="flex items-center space-x-1">
                  <Calendar className="h-3 w-3" />
                  <span>Created {created}</span>
                </div>
                <div className="flex items-center space-x-1">
                  <span>{executions} executions</span>
                </div>
                {lastRun && (
                  <div className="flex items-center space-x-1">
                    <Clock className="h-3 w-3" />
                    <span>Last run {lastRun}</span>
                  </div>
                )}
              </div>
            </div>
          </div>
          
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => onViewDetails?.(id)}>
                View Details
              </DropdownMenuItem>
              {status === "active" ? (
                <DropdownMenuItem onClick={() => onStatusChange?.(id, "paused")}>
                  <Pause className="h-4 w-4 mr-2" />
                  Pause Workflow
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={() => onStatusChange?.(id, "active")}>
                  <Play className="h-4 w-4 mr-2" />
                  Activate Workflow
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onEdit?.(id)}>
                <Settings className="h-4 w-4 mr-2" />
                Edit Settings
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onDuplicate?.(id)}>
                <Copy className="h-4 w-4 mr-2" />
                Duplicate
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="text-red-600" onClick={() => onDelete?.(id)}>
                <Trash2 className="h-4 w-4 mr-2" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardContent>
    </Card>
  )
}
