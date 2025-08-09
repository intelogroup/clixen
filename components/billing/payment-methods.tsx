"use client"

import { CreditCard, Plus, Settings, Trash2, CheckCircle, Circle, Shield, Lock, Briefcase } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface PaymentCardProps {
  type: "visa" | "mastercard"
  last4: string
  expiry: string
  isPrimary: boolean
}

function PaymentCard({ type, last4, expiry, isPrimary }: PaymentCardProps) {
  return (
    <div className="border rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <CreditCard className="h-4 w-4" />
          <span>•••• •••• •••• {last4}</span>
        </div>
        <div className="flex items-center space-x-2">
          {isPrimary ? (
            <Badge className="bg-green-100 text-green-800">
              <CheckCircle className="h-3 w-3 mr-1" />
              Primary
            </Badge>
          ) : (
            <Badge variant="outline">
              <Circle className="h-3 w-3 mr-1" />
              Backup
            </Badge>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {type === "visa" ? "Visa" : "Mastercard"} ending in {last4}
        </div>
        <div className="flex space-x-2">
          <Button variant="ghost" size="sm">
            <Settings className="h-4 w-4 mr-1" />
            Edit
          </Button>
          <Button variant="ghost" size="sm" className="text-red-600">
            <Trash2 className="h-4 w-4 mr-1" />
            Remove
          </Button>
        </div>
      </div>
      <div className="text-sm text-muted-foreground">
        Expires: {expiry}
      </div>
    </div>
  )
}

export function PaymentMethods() {
  const securityFeatures = [
    { icon: "🛡️", label: "PCI DSS Compliant" },
    { icon: "🔐", label: "256-bit SSL encryption" }, 
    { icon: "💼", label: "Stripe secure processing" }
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <CreditCard className="h-5 w-5" />
            <span>PAYMENT METHODS</span>
          </div>
          <Button variant="outline" size="sm">
            <Plus className="h-4 w-4 mr-2" />
            Add Payment
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div>
          <h4 className="font-semibold mb-4">💳 Primary Payment Method</h4>
          <div className="space-y-3">
            <PaymentCard 
              type="visa"
              last4="4242"
              expiry="12/2027"
              isPrimary={true}
            />
            <PaymentCard 
              type="mastercard"
              last4="5555"
              expiry="08/2026"
              isPrimary={false}
            />
          </div>
        </div>

        <div>
          <h4 className="font-semibold mb-3">🔒 Payment Security</h4>
          <div className="space-y-2">
            {securityFeatures.map((feature, index) => (
              <div key={index} className="flex items-center space-x-2">
                <span>{feature.icon}</span>
                <span className="text-sm">{feature.label}</span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
