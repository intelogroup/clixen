"use client"

import { Settings, User } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { ChatMessage } from "./chat-message"
import { ChatInput } from "./chat-input"

interface ChatMainProps {
  chatTitle?: string
  messages: Array<{
    id: number
    type: "ai" | "user"
    content: string
    timestamp: string
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
  }>
  onSendMessage: (message: string) => void
  isCreatingWorkflow?: boolean
}

export function ChatMain({ 
  chatTitle = "New Workflow Chat",
  messages, 
  onSendMessage, 
  isCreatingWorkflow = false 
}: ChatMainProps) {
  return (
    <div className="flex-1 flex flex-col h-full">
      {/* Header */}
      <div className="border-b bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
            <h2 className="font-semibold">{chatTitle}</h2>
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-sm text-muted-foreground">John D.</span>
            <Avatar className="h-7 w-7">
              <AvatarFallback>
                <User className="h-4 w-4" />
              </AvatarFallback>
            </Avatar>
            <Button variant="ghost" size="sm" className="p-2">
              <Settings className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages.map((message) => (
          <ChatMessage
            key={message.id}
            type={message.type}
            content={message.content}
            timestamp={message.timestamp}
            workflowSummary={message.workflowSummary}
            progress={message.progress}
          />
        ))}
      </div>

      {/* Input Area */}
      <div className="border-t bg-white p-6">
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">Describe your workflow...</p>
          <ChatInput 
            onSendMessage={onSendMessage}
            disabled={isCreatingWorkflow}
            placeholder="Type your message..."
          />
        </div>
      </div>
    </div>
  )
}
