"use client"

import { Bot, User, Clock, CheckCircle, Loader2, Zap, Smartphone, Settings, AlertTriangle, ExternalLink, Eye, Wrench, Archive, Play, TestTube } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"

interface WorkflowSummary {
  name: string
  trigger: string
  action: string
  output: string
  status?: "active" | "pending" | "error"
  workflowId?: string
  nextRun?: string
}

interface WorkflowProgress {
  step: string
  status: "completed" | "in-progress" | "pending" | "error"
  progress?: number
}

interface QuestionOption {
  label: string
  value: string
}

interface ActionButton {
  label: string
  action: string
  variant?: "default" | "outline" | "destructive" | "secondary"
  icon?: React.ReactNode
}

interface ChatMessageProps {
  type: "ai" | "user"
  content: string
  timestamp?: string
  isLoading?: boolean
  workflowSummary?: WorkflowSummary
  progress?: WorkflowProgress[]
  questions?: string[]
  options?: QuestionOption[]
  actionButtons?: ActionButton[]
  errorMessage?: string
  samplePreview?: {
    title: string
    content: string
  }
  onAction?: (action: string, value?: string) => void
}

export function ChatMessage({
  type,
  content,
  timestamp,
  isLoading = false,
  workflowSummary,
  progress,
  questions,
  options,
  actionButtons,
  errorMessage,
  samplePreview,
  onAction
}: ChatMessageProps) {
  const isAI = type === "ai"

  return (
    <div className={cn(
      "flex items-start space-x-3 w-full",
      !isAI && "flex-row-reverse space-x-reverse"
    )}>
      <Avatar className={cn("w-8 h-8", isAI ? "bg-blue-100" : "bg-gray-100")}>
        <AvatarFallback>
          {isAI ? <Bot className="h-4 w-4 text-blue-600" /> : <User className="h-4 w-4 text-gray-600" />}
        </AvatarFallback>
      </Avatar>

      <div className={cn("max-w-2xl", !isAI && "max-w-md")}>
        <Card className={cn(
          "shadow-sm",
          isAI ? "bg-gray-50 border-gray-200" : "bg-blue-50 border-blue-200",
          errorMessage && "border-red-200 bg-red-50"
        )}>
          <CardContent className="p-4">
            {isLoading ? (
              <div className="flex items-center space-x-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm text-muted-foreground">Thinking...</span>
              </div>
            ) : (
              <>
                <div className="text-gray-800 whitespace-pre-wrap">{content}</div>

                {/* Error Message */}
                {errorMessage && (
                  <div className="mt-4 p-3 bg-red-100 border border-red-200 rounded-lg">
                    <div className="flex items-start space-x-2">
                      <AlertTriangle className="h-4 w-4 text-red-600 mt-0.5 flex-shrink-0" />
                      <div className="text-sm text-red-800">{errorMessage}</div>
                    </div>
                  </div>
                )}

                {/* Workflow Summary */}
                {workflowSummary && (
                  <div className="mt-4 p-4 bg-white rounded-lg border">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="font-semibold text-gray-900">{workflowSummary.name}</h4>
                      <Badge variant={workflowSummary.status === "active" ? "default" : workflowSummary.status === "error" ? "destructive" : "secondary"}>
                        {workflowSummary.status || "pending"}
                      </Badge>
                    </div>
                    <div className="space-y-2 text-sm">
                      <div><span className="font-medium text-gray-600">Trigger:</span> {workflowSummary.trigger}</div>
                      <div><span className="font-medium text-gray-600">Action:</span> {workflowSummary.action}</div>
                      <div><span className="font-medium text-gray-600">Output:</span> {workflowSummary.output}</div>
                      {workflowSummary.nextRun && (
                        <div><span className="font-medium text-gray-600">Next run:</span> {workflowSummary.nextRun}</div>
                      )}
                      {workflowSummary.workflowId && (
                        <div><span className="font-medium text-gray-600">ID:</span> <code className="text-xs bg-gray-100 px-1 rounded">{workflowSummary.workflowId}</code></div>
                      )}
                    </div>
                  </div>
                )}

                {/* Progress Steps */}
                {progress && (
                  <div className="mt-4 space-y-3">
                    {progress.map((step, index) => (
                      <div key={index} className="space-y-2">
                        <div className="flex items-center space-x-2 text-sm">
                          {step.status === "completed" && <CheckCircle className="h-4 w-4 text-green-500" />}
                          {step.status === "in-progress" && <Loader2 className="h-4 w-4 animate-spin text-blue-500" />}
                          {step.status === "pending" && <Clock className="h-4 w-4 text-gray-400" />}
                          {step.status === "error" && <AlertTriangle className="h-4 w-4 text-red-500" />}
                          <span className={cn(
                            step.status === "completed" && "text-green-700",
                            step.status === "in-progress" && "text-blue-700",
                            step.status === "pending" && "text-gray-500",
                            step.status === "error" && "text-red-700"
                          )}>
                            {step.step}
                          </span>
                        </div>
                        {step.progress !== undefined && step.status === "in-progress" && (
                          <Progress value={step.progress} className="h-2" />
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Questions */}
                {questions && questions.length > 0 && (
                  <div className="mt-4 p-3 bg-blue-50 rounded-lg border border-blue-200">
                    <div className="space-y-2">
                      {questions.map((question, index) => (
                        <div key={index} className="text-sm text-blue-800">
                          {index + 1}. {question}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Options */}
                {options && options.length > 0 && (
                  <div className="mt-4 space-y-2">
                    {options.map((option, index) => (
                      <Button
                        key={index}
                        variant="outline"
                        className="w-full justify-start text-left h-auto p-3"
                        onClick={() => onAction?.("option", option.value)}
                      >
                        <span className="text-sm">{option.label}</span>
                      </Button>
                    ))}
                  </div>
                )}

                {/* Sample Preview */}
                {samplePreview && (
                  <div className="mt-4 p-4 bg-gradient-to-r from-gray-50 to-gray-100 rounded-lg border">
                    <h5 className="font-medium text-gray-900 mb-2">{samplePreview.title}</h5>
                    <div className="p-3 bg-white rounded border text-sm font-mono text-gray-700">
                      {samplePreview.content}
                    </div>
                  </div>
                )}

                {/* Action Buttons */}
                {actionButtons && actionButtons.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {actionButtons.map((button, index) => (
                      <Button
                        key={index}
                        variant={button.variant || "outline"}
                        size="sm"
                        onClick={() => onAction?.(button.action)}
                        className="h-auto py-2"
                      >
                        {button.icon && <span className="mr-2">{button.icon}</span>}
                        {button.label}
                      </Button>
                    ))}
                  </div>
                )}
              </>
            )}

            {timestamp && (
              <p className="text-xs text-muted-foreground mt-3">{timestamp}</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
