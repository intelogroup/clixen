"use client"

import { FileText, Mail, Calendar, DollarSign } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface InvoiceItemProps {
  date: string
  invoiceNumber: string
  amount: string
  status: "paid" | "free"
  description: string
}

function InvoiceItem({ date, invoiceNumber, amount, status, description }: InvoiceItemProps) {
  return (
    <div className="flex items-center justify-between p-3 border rounded-lg">
      <div className="space-y-1">
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">{date}</span>
          </div>
          <span className="text-sm text-muted-foreground">{invoiceNumber}</span>
          <div className="flex items-center space-x-2">
            <DollarSign className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">{amount}</span>
          </div>
          <Badge className={status === "paid" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800"}>
            {status === "paid" ? "✅ Paid" : "✅ Free"}
          </Badge>
        </div>
        <div className="text-sm text-muted-foreground">{description}</div>
      </div>
      <div className="flex space-x-2">
        <Button variant="ghost" size="sm">
          <FileText className="h-4 w-4 mr-1" />
          PDF
        </Button>
        <Button variant="ghost" size="sm">
          <Mail className="h-4 w-4 mr-1" />
          Resend
        </Button>
      </div>
    </div>
  )
}

export function BillingHistory() {
  const invoices = [
    {
      date: "Aug 1, 2025",
      invoiceNumber: "Invoice #INV-001234",
      amount: "$29.00",
      status: "paid" as const,
      description: "Clixen Pro Monthly"
    },
    {
      date: "Jul 1, 2025", 
      invoiceNumber: "Invoice #INV-001233",
      amount: "$29.00",
      status: "paid" as const,
      description: "Clixen Pro Monthly"
    },
    {
      date: "Jun 1, 2025",
      invoiceNumber: "Invoice #INV-001232", 
      amount: "$19.00",
      status: "paid" as const,
      description: "Clixen Basic Monthly"
    },
    {
      date: "May 1, 2025",
      invoiceNumber: "Invoice #INV-001231",
      amount: "$0.00", 
      status: "free" as const,
      description: "Trial Period"
    }
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>📋 BILLING HISTORY</span>
          <Button variant="outline" size="sm">
            <FileText className="h-4 w-4 mr-2" />
            View All
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div>
          <h4 className="font-semibold mb-4">Recent Invoices</h4>
          <div className="space-y-3">
            {invoices.map((invoice, index) => (
              <InvoiceItem key={index} {...invoice} />
            ))}
          </div>
        </div>

        <div className="border-t pt-4">
          <h4 className="font-semibold mb-2">📊 Spending Summary</h4>
          <div className="text-sm text-muted-foreground">
            This month: $29.00 • This year: $261.00 • All time: $261.00
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
