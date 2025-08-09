"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Button } from "@/components/ui/button"
import { BarChart3 } from "lucide-react"

interface UsageItemProps {
  icon: string
  label: string
  current: number | string
  limit: number | string
  percentage: number
  unit?: string
}

function UsageItem({ icon, label, current, limit, percentage, unit = "" }: UsageItemProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <span>{icon}</span>
          <span className="font-medium">{label}</span>
        </div>
        <div className="text-sm text-muted-foreground">
          {current} / {limit} {unit} [{percentage}%]
        </div>
      </div>
      <Progress value={percentage} className="h-2" />
    </div>
  )
}

export function UsageTracking() {
  const usageItems = [
    {
      icon: "⚡",
      label: "Workflow Executions",
      current: "2,847",
      limit: "10,000",
      percentage: 28.5
    },
    {
      icon: "🤖",
      label: "AI Requests", 
      current: "1,205",
      limit: "5,000",
      percentage: 24.1
    },
    {
      icon: "💾",
      label: "Storage Used",
      current: "1.2GB",
      limit: "5GB", 
      percentage: 24.0
    },
    {
      icon: "📞",
      label: "API Calls",
      current: "45,023",
      limit: "100,000",
      percentage: 45.0
    },
    {
      icon: "📧",
      label: "Email Notifications",
      current: "156",
      limit: "1,000",
      percentage: 15.6
    }
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>USAGE THIS MONTH</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {usageItems.map((item, index) => (
          <UsageItem key={index} {...item} />
        ))}
        
        <div className="flex items-center justify-between pt-4 border-t">
          <div className="flex items-center space-x-2">
            <span>🎯</span>
            <span className="font-medium">Performance Score: 94/100</span>
          </div>
          <Button variant="outline" size="sm">
            <BarChart3 className="h-4 w-4 mr-2" />
            View Detailed Analytics
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
