"use client"

import { Crown, RotateCcw, Check, TrendingUp, TrendingDown, X } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

export function CurrentPlan() {
  const features = [
    "Unlimited workflows",
    "10,000 executions/month", 
    "Priority support",
    "Advanced analytics",
    "Team collaboration (5 users)"
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Crown className="h-5 w-5 text-yellow-500" />
            <span>CLIXEN PRO</span>
          </div>
          <Button variant="outline" size="sm">
            <RotateCcw className="h-4 w-4 mr-2" />
            Manage Plan
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-2xl font-bold">$29/month</span>
            <span className="text-sm text-muted-foreground">Billed monthly</span>
          </div>
          <div className="text-sm text-muted-foreground">
            📅 Next billing: August 25, 2025
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm">🔄 Auto-renew: ON</span>
            <Button variant="outline" size="sm">Toggle Off</Button>
          </div>
        </div>

        <div>
          <h4 className="font-semibold mb-3">📊 PLAN FEATURES:</h4>
          <div className="space-y-2">
            {features.map((feature, index) => (
              <div key={index} className="flex items-center space-x-2">
                <Check className="h-4 w-4 text-green-500" />
                <span className="text-sm">{feature}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex space-x-2">
          <Button className="flex-1">
            <TrendingUp className="h-4 w-4 mr-2" />
            Upgrade to Enterprise
          </Button>
          <Button variant="outline" className="flex-1">
            <TrendingDown className="h-4 w-4 mr-2" />
            Downgrade Plan
          </Button>
          <Button variant="destructive" className="flex-1">
            <X className="h-4 w-4 mr-2" />
            Cancel Subscription
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
