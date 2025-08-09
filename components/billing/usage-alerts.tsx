"use client"

import { AlertTriangle, Settings, Mail, Plus, Edit } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

export function UsageAlerts() {
  const alerts = [
    { label: "Warn at 80% of execution limit", enabled: true },
    { label: "Alert at 95% of execution limit", enabled: true }, 
    { label: "Notify before billing date (3 days)", enabled: true },
    { label: "Weekly usage summary emails", enabled: false }
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="h-5 w-5" />
            <span>USAGE ALERTS & NOTIFICATIONS</span>
          </div>
          <Button variant="outline" size="sm">
            <Settings className="h-4 w-4 mr-2" />
            Configure
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div>
          <h4 className="font-semibold mb-4">🚨 Set Usage Alerts</h4>
          <div className="space-y-3">
            {alerts.map((alert, index) => (
              <div key={index} className="flex items-center space-x-3">
                <span className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                  alert.enabled 
                    ? "bg-green-500 border-green-500 text-white" 
                    : "bg-red-500 border-red-500 text-white"
                }`}>
                  {alert.enabled ? "✓" : "✗"}
                </span>
                <span className="text-sm">{alert.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h4 className="font-semibold mb-4">📧 Billing Contact</h4>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Mail className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm">Primary: john.doe@example.com</span>
              </div>
              <Button variant="ghost" size="sm">
                <Edit className="h-4 w-4 mr-1" />
                Change
              </Button>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Mail className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm">CC: billing@mycompany.com</span>
              </div>
              <Button variant="ghost" size="sm">
                <Plus className="h-4 w-4 mr-1" />
                Add More
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
