"use client"

import { Bot } from "lucide-react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Card, CardContent } from "@/components/ui/card"

export function TypingIndicator() {
  return (
    <div className="flex items-start space-x-3 w-full">
      <Avatar className="w-8 h-8 bg-blue-100">
        <AvatarFallback>
          <Bot className="h-4 w-4 text-blue-600" />
        </AvatarFallback>
      </Avatar>
      
      <div className="max-w-xl">
        <Card className="shadow-sm bg-gray-50 border-gray-200">
          <CardContent className="p-4">
            <div className="flex items-center space-x-1">
              <div className="flex space-x-1">
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
              </div>
              <span className="text-sm text-muted-foreground ml-2">AI is thinking...</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
