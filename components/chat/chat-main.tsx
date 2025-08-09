"use client"

import { Settings, User, Bot } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { ChatMessage } from "./chat-message"
import { ChatInput } from "./chat-input"
import { QuickSuggestions } from "./quick-suggestions"

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
    showSuggestions?: boolean
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
      {/* Professional Header */}
      <div className="border-b bg-gradient-to-r from-blue-50 to-indigo-50 px-6 py-6">
        <div className="text-center space-y-2">
          <div className="flex items-center justify-center space-x-2">
            <Bot className="h-6 w-6 text-blue-600" />
            <h1 className="text-2xl font-bold text-gray-900">CLIXEN AI WORKFLOW CREATOR</h1>
          </div>
          <p className="text-gray-600">Create amazing workflows with natural language</p>
        </div>
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages.map((message) => (
          <div key={message.id}>
            <ChatMessage
              type={message.type}
              content={message.content}
              timestamp={message.timestamp}
              workflowSummary={message.workflowSummary}
              progress={message.progress}
            />
            {message.showSuggestions && (
              <div className="mt-4">
                <QuickSuggestions onSuggestionClick={onSendMessage} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Enhanced Input Area */}
      <div className="border-t bg-white p-6">
        <ChatInput
          onSendMessage={onSendMessage}
          disabled={isCreatingWorkflow}
          placeholder="Tell me about your workflow..."
        />
      </div>
    </div>
  )
}
