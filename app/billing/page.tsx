"use client"

import { BillingHeader } from "@/components/billing/billing-header"
import { CurrentPlan } from "@/components/billing/current-plan"
import { UsageTracking } from "@/components/billing/usage-tracking"
import { PaymentMethods } from "@/components/billing/payment-methods"
import { BillingHistory } from "@/components/billing/billing-history"
import { UsageAlerts } from "@/components/billing/usage-alerts"
import { PlanComparison } from "@/components/billing/plan-comparison"

export default function BillingPage() {
  return (
    <div className="min-h-screen bg-background">
      <BillingHeader activeTab="settings" />
      
      <div className="container mx-auto px-6 py-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">💳 Billing & Subscription</h1>
        </div>
        
        <div className="space-y-6">
          <CurrentPlan />
          <UsageTracking />
          <PaymentMethods />
          <BillingHistory />
          <UsageAlerts />
          <PlanComparison />
        </div>
      </div>
    </div>
  )
}
