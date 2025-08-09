"use client"

import {
  Facebook, Twitter, Instagram, Linkedin, Calendar, Image, Video, BarChart,
  Mail, Cloud, Shield, Zap, Globe, Database, Bell, Users, FileText,
  TrendingUp, Clock, Target, Smartphone
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface QuickSuggestionsProps {
  onSuggestionClick: (suggestion: string) => void
}

export function QuickSuggestions({ onSuggestionClick }: QuickSuggestionsProps) {
  const workflowCategories = [
    {
      title: "Customer Communication",
      icon: Mail,
      color: "text-blue-600",
      suggestions: [
        { text: "Send welcome emails to new customers", icon: Users },
        { text: "Create appointment reminder system", icon: Clock },
        { text: "Set up customer feedback surveys", icon: Target },
        { text: "Automate newsletter distribution", icon: Mail }
      ]
    },
    {
      title: "Sales & Marketing",
      icon: TrendingUp,
      color: "text-green-600",
      suggestions: [
        { text: "Automate lead capture and CRM sync", icon: Database },
        { text: "Schedule social media posts", icon: Calendar },
        { text: "Generate and send invoices automatically", icon: FileText },
        { text: "Track and report on marketing metrics", icon: BarChart }
      ]
    },
    {
      title: "Operations & Admin",
      icon: Shield,
      color: "text-purple-600",
      suggestions: [
        { text: "Backup important files to cloud storage weekly", icon: Cloud },
        { text: "Monitor website uptime and send alerts", icon: Globe },
        { text: "Create daily operational reports", icon: FileText },
        { text: "Automate expense tracking and reporting", icon: BarChart }
      ]
    },
    {
      title: "Quick Starters",
      icon: Zap,
      color: "text-orange-600",
      suggestions: [
        { text: "Send me daily weather updates every morning", icon: Bell },
        { text: "Post motivational quotes to Instagram daily", icon: Smartphone },
        { text: "Backup my Dropbox to Google Drive weekly", icon: Cloud },
        { text: "Send me alerts when my website goes down", icon: Globe }
      ]
    }
  ]

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h3 className="text-lg font-semibold text-gray-900 mb-2">🚀 Popular Workflow Ideas</h3>
        <p className="text-sm text-gray-600 mb-6">
          Click any suggestion below to get started, or describe your own automation idea!
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {workflowCategories.map((category) => (
          <Card key={category.title} className="hover:shadow-md transition-shadow">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center space-x-2 text-sm font-semibold">
                <category.icon className={`h-4 w-4 ${category.color}`} />
                <span>{category.title}</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {category.suggestions.map((suggestion, index) => (
                  <Button
                    key={index}
                    variant="ghost"
                    className="w-full justify-start h-auto p-3 text-left hover:bg-gray-50"
                    onClick={() => onSuggestionClick(suggestion.text)}
                  >
                    <suggestion.icon className="h-4 w-4 mr-3 text-gray-500 flex-shrink-0" />
                    <span className="text-sm text-gray-700">{suggestion.text}</span>
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Popular Templates Section */}
      <Card className="bg-gradient-to-r from-blue-50 to-indigo-50 border-blue-200">
        <CardHeader>
          <CardTitle className="flex items-center space-x-2 text-sm font-semibold text-blue-900">
            <Zap className="h-4 w-4 text-blue-600" />
            <span>Try These Popular Templates</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Button
              variant="outline"
              className="h-auto p-4 bg-white hover:bg-blue-50 border-blue-200"
              onClick={() => onSuggestionClick("Create a customer onboarding workflow that sends welcome emails, adds users to CRM, and schedules follow-ups")}
            >
              <div className="text-left">
                <div className="font-medium text-blue-900">Customer Onboarding</div>
                <div className="text-xs text-blue-600">Multi-step automation</div>
              </div>
            </Button>

            <Button
              variant="outline"
              className="h-auto p-4 bg-white hover:bg-blue-50 border-blue-200"
              onClick={() => onSuggestionClick("Set up social media automation that posts to Facebook, Twitter, Instagram, and LinkedIn on a schedule")}
            >
              <div className="text-left">
                <div className="font-medium text-blue-900">Social Media Suite</div>
                <div className="text-xs text-blue-600">Multi-platform posting</div>
              </div>
            </Button>

            <Button
              variant="outline"
              className="h-auto p-4 bg-white hover:bg-blue-50 border-blue-200"
              onClick={() => onSuggestionClick("Create a comprehensive backup system that saves files from multiple sources to different cloud storage providers")}
            >
              <div className="text-left">
                <div className="font-medium text-blue-900">Smart Backup System</div>
                <div className="text-xs text-blue-600">Multi-source protection</div>
              </div>
            </Button>

            <Button
              variant="outline"
              className="h-auto p-4 bg-white hover:bg-blue-50 border-blue-200"
              onClick={() => onSuggestionClick("Build a monitoring dashboard that tracks website uptime, performance metrics, and sends alerts")}
            >
              <div className="text-left">
                <div className="font-medium text-blue-900">Monitoring Dashboard</div>
                <div className="text-xs text-blue-600">Real-time alerts</div>
              </div>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
