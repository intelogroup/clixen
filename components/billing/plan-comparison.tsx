"use client"

import { Crown, Phone } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface PlanCardProps {
  name: string
  price: string
  isCurrent?: boolean
  features: string[]
  buttonText: string
  buttonVariant?: "default" | "outline" | "secondary"
}

function PlanCard({ name, price, isCurrent = false, features, buttonText, buttonVariant = "default" }: PlanCardProps) {
  return (
    <div className="border rounded-lg p-4 space-y-4 relative">
      {isCurrent && (
        <Badge className="absolute -top-2 left-1/2 transform -translate-x-1/2 bg-yellow-500 text-white">
          <Crown className="h-3 w-3 mr-1" />
          CURRENT
        </Badge>
      )}
      <div className="text-center">
        <h3 className="font-bold text-lg">{name}</h3>
        <div className="text-2xl font-bold text-primary">{price}</div>
      </div>
      <div className="space-y-2">
        {features.map((feature, index) => (
          <div key={index} className="text-sm text-center">{feature}</div>
        ))}
      </div>
      <Button 
        className="w-full" 
        variant={buttonVariant}
        disabled={isCurrent}
      >
        {buttonText}
      </Button>
    </div>
  )
}

export function PlanComparison() {
  const plans = [
    {
      name: "FREE",
      price: "$0/month",
      features: [
        "5 workflows",
        "100 exec/mo", 
        "Basic AI"
      ],
      buttonText: "Choose Free",
      buttonVariant: "outline" as const
    },
    {
      name: "BASIC", 
      price: "$9/month",
      features: [
        "50 workflows",
        "1K exec/mo",
        "Basic AI"
      ],
      buttonText: "Downgrade",
      buttonVariant: "outline" as const
    },
    {
      name: "PRO",
      price: "$29/month", 
      isCurrent: true,
      features: [
        "Unlimited",
        "10K exec/mo",
        "Advanced AI",
        "Priority"
      ],
      buttonText: "Current",
      buttonVariant: "secondary" as const
    },
    {
      name: "ENTERPRISE",
      price: "Custom",
      features: [
        "Unlimited", 
        "Custom limits",
        "Premium AI",
        "Dedicated"
      ],
      buttonText: "Contact Sales",
      buttonVariant: "default" as const
    }
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>PLAN COMPARISON</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {plans.map((plan, index) => (
            <PlanCard key={index} {...plan} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
