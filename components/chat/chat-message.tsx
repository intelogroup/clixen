"use client"

import { Bot, User, Clock, CheckCircle, Loader2, Zap, Smartphone, Settings } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { cn } from "@/lib/utils"

interface ChatMessageProps {
  type: "ai" | "user"
  content: string
  timestamp?: string
  isLoading?: boolean
  workflowSummary?: {
    name: string
    trigger: string
    action: string
    output: string
  }
  progress?: Array<{
    step: string
    status: "completed" | "in-progress" | "pending"
  }>
}

export function ChatMessage({ 
  type, 
  content, 
  timestamp, 
  isLoading = false,
  workflowSummary,
  progress 
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
      
      <div className={cn("max-w-xl", !isAI && "max-w-md")}>
        <Card className={cn(
          "shadow-sm",
          isAI ? "bg-gray-50 border-gray-200" : "bg-blue-50 border-blue-200"
        )}>
          <CardContent className="p-4">
            {isLoading ? (
              <div className="flex items-center space-x-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm text-muted-foreground">Thinking...</span>
              </div>
            ) : (
              <>
                <p className="text-gray-800 whitespace-pre-wrap">{content}</p>
                
                {workflowSummary && (
                  <div className="mt-4 p-3 bg-white rounded-lg border">
                    <h4 className="font-semibold text-sm text-gray-900 mb-2">Workflow Summary</h4>
                    <div className="space-y-1 text-sm">
                      <div><span className="font-medium">Trigger:</span> {workflowSummary.trigger}</div>
                      <div><span className="font-medium">Action:</span> {workflowSummary.action}</div>
                      <div><span className="font-medium">Output:</span> {workflowSummary.output}</div>
                    </div>
                  </div>
                )}
                
                {progress && (
                  <div className="mt-4 space-y-2">
                    {progress.map((step, index) => (
                      <div key={index} className="flex items-center space-x-2 text-sm">
                        {step.status === "completed" && <CheckCircle className="h-4 w-4 text-green-500" />}
                        {step.status === "in-progress" && <Loader2 className="h-4 w-4 animate-spin text-blue-500" />}
                        {step.status === "pending" && <Clock className="h-4 w-4 text-gray-400" />}
                        <span className={cn(
                          step.status === "completed" && "text-green-700",
                          step.status === "in-progress" && "text-blue-700",
                          step.status === "pending" && "text-gray-500"
                        )}>
                          {step.step}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
            
            {timestamp && (
              <p className="text-xs text-muted-foreground mt-2">{timestamp}</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
