"use client"

import { useState } from "react"
import { Send, Paperclip, Mic } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface ChatInputProps {
  onSendMessage: (message: string) => void
  disabled?: boolean
  placeholder?: string
}

export function ChatInput({ 
  onSendMessage, 
  disabled = false,
  placeholder = "Describe the workflow you'd like to create..."
}: ChatInputProps) {
  const [message, setMessage] = useState("")
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (message.trim() && !disabled) {
      onSendMessage(message.trim())
      setMessage("")
    }
  }
  
  return (
    <div className="border-t bg-white p-4">
      <form onSubmit={handleSubmit} className="flex items-center space-x-2">
        <div className="flex-1 relative">
          <Input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder={disabled ? "AI is creating your workflow..." : placeholder}
            disabled={disabled}
            className="pr-20"
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center space-x-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0"
              disabled={disabled}
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0"
              disabled={disabled}
            >
              <Mic className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <Button 
          type="submit" 
          disabled={!message.trim() || disabled}
          className="px-4"
        >
          <Send className="h-4 w-4 mr-1" />
          Send
        </Button>
      </form>
    </div>
  )
}
