"use client"

import { Facebook, Twitter, Instagram, Linkedin, Calendar, Image, Video, BarChart } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface QuickSuggestionsProps {
  onSuggestionClick: (suggestion: string) => void
}

export function QuickSuggestions({ onSuggestionClick }: QuickSuggestionsProps) {
  const platforms = [
    { name: "Facebook", icon: Facebook, color: "text-blue-600" },
    { name: "Twitter/X", icon: Twitter, color: "text-gray-800" },
    { name: "Instagram", icon: Instagram, color: "text-pink-600" },
    { name: "LinkedIn", icon: Linkedin, color: "text-blue-700" }
  ]

  const templates = [
    { name: "Daily Posts", icon: Calendar, description: "Schedule regular content" },
    { name: "Image Carousel", icon: Image, description: "Multi-image posts" },
    { name: "Video Content", icon: Video, description: "Video publishing" },
    { name: "Analytics Reports", icon: BarChart, description: "Performance tracking" }
  ]

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold text-gray-700">Quick Suggestions</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {platforms.map((platform) => (
              <Button
                key={platform.name}
                variant="ghost"
                className="w-full justify-start h-auto p-3"
                onClick={() => onSuggestionClick(`Add ${platform.name} to my social media automation`)}
              >
                <platform.icon className={`h-4 w-4 mr-3 ${platform.color}`} />
                <span className="text-sm">{platform.name}</span>
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold text-gray-700">Popular Templates</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {templates.map((template) => (
              <Button
                key={template.name}
                variant="ghost"
                className="w-full justify-start h-auto p-3"
                onClick={() => onSuggestionClick(`Create a ${template.name.toLowerCase()} workflow`)}
              >
                <template.icon className="h-4 w-4 mr-3 text-gray-600" />
                <div className="text-left">
                  <div className="text-sm font-medium">{template.name}</div>
                  <div className="text-xs text-muted-foreground">{template.description}</div>
                </div>
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
