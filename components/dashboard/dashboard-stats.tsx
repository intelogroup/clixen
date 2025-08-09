"use client"

import { Activity, Zap, CheckCircle, Clock, DollarSign, BarChart3 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface StatCardProps {
  title: string
  value: string | number
  change?: string
  changeType?: "positive" | "negative" | "neutral"
  icon: React.ComponentType<{ className?: string }>
}

function StatCard({ title, value, change, changeType = "neutral", icon: Icon }: StatCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {change && (
          <p className={`text-xs ${
            changeType === "positive" ? "text-green-600" :
            changeType === "negative" ? "text-red-600" :
            "text-muted-foreground"
          }`}>
            {change}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function DashboardStats() {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-6">
      <StatCard
        title="Active Workflows"
        value={12}
        change="+3 this week"
        changeType="positive"
        icon={Activity}
      />
      <StatCard
        title="Total Executions"
        value="2,847"
        change="+421 this week"
        changeType="positive"
        icon={Zap}
      />
      <StatCard
        title="Success Rate"
        value="94.2%"
        change="+2.1% this week"
        changeType="positive"
        icon={CheckCircle}
      />
      <StatCard
        title="Avg Response Time"
        value="2.3s"
        change="-0.4s this week"
        changeType="positive"
        icon={Clock}
      />
      <StatCard
        title="Total Cost"
        value="$47.23"
        change="+$12.40 this week"
        changeType="negative"
        icon={DollarSign}
      />
      <StatCard
        title="API Usage"
        value="8,234/10,000"
        change="82% used"
        changeType="neutral"
        icon={BarChart3}
      />
    </div>
  )
}
