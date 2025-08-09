"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { 
  Key, 
  Mail, 
  Globe, 
  ExternalLink, 
  Info, 
  Copy, 
  RefreshCw, 
  Check,
  Loader2,
  Lock,
  Eye,
  EyeOff
} from "lucide-react"

interface APIService {
  id: string
  name: string
  icon: any
  required: boolean
  status: "pending" | "connected" | "ready"
  placeholder: string
  helpUrl?: string
  description: string
  type: "api-key" | "oauth" | "webhook"
  value?: string
}

interface APISetupModalProps {
  isOpen: boolean
  onSave: (services: Record<string, string>) => void
  onCancel: () => void
  onSkip: () => void
}

export function APISetupModal({
  isOpen,
  onSave,
  onCancel,
  onSkip
}: APISetupModalProps) {
  const [services, setServices] = useState<APIService[]>([
    {
      id: "openai",
      name: "OpenAI API Key",
      icon: Key,
      required: true,
      status: "pending",
      placeholder: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      helpUrl: "https://platform.openai.com/api-keys",
      description: "Required for AI-powered workflow generation",
      type: "api-key"
    },
    {
      id: "gmail",
      name: "Gmail/Email Service",
      icon: Mail,
      required: false,
      status: "pending",
      placeholder: "Your app password here...",
      helpUrl: "/help/gmail-setup",
      description: "For email automation workflows",
      type: "api-key"
    },
    {
      id: "webhook",
      name: "Webhook URL (Auto-generated)",
      icon: Globe,
      required: false,
      status: "ready",
      placeholder: "https://api.clixen.app/hooks/usr_123",
      description: "For receiving webhook notifications",
      type: "webhook",
      value: "https://api.clixen.app/hooks/usr_123"
    }
  ])

  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({})
  const [isLoading, setIsLoading] = useState(false)
  const [copiedFields, setCopiedFields] = useState<Record<string, boolean>>({})

  if (!isOpen) return null

  const updateService = (id: string, value: string) => {
    setServices(prev => prev.map(service => 
      service.id === id 
        ? { ...service, value, status: value.trim() ? "connected" : "pending" }
        : service
    ))
  }

  const toggleKeyVisibility = (serviceId: string) => {
    setShowKeys(prev => ({ ...prev, [serviceId]: !prev[serviceId] }))
  }

  const copyToClipboard = async (text: string, fieldId: string) => {
    await navigator.clipboard.writeText(text)
    setCopiedFields(prev => ({ ...prev, [fieldId]: true }))
    setTimeout(() => {
      setCopiedFields(prev => ({ ...prev, [fieldId]: false }))
    }, 2000)
  }

  const regenerateWebhook = () => {
    const newWebhookUrl = `https://api.clixen.app/hooks/usr_${Math.random().toString(36).substr(2, 9)}`
    updateService("webhook", newWebhookUrl)
  }

  const handleSave = async () => {
    setIsLoading(true)
    
    // Simulate API call
    setTimeout(() => {
      const serviceValues = services.reduce((acc, service) => {
        if (service.value) {
          acc[service.id] = service.value
        }
        return acc
      }, {} as Record<string, string>)
      
      onSave(serviceValues)
      setIsLoading(false)
    }, 1500)
  }

  const getStatusBadge = (status: string, required: boolean) => {
    switch (status) {
      case "connected":
        return <Badge className="bg-green-100 text-green-800">Connected</Badge>
      case "ready":
        return <Badge className="bg-blue-100 text-blue-800">Ready</Badge>
      default:
        return (
          <Badge variant={required ? "destructive" : "secondary"}>
            {required ? "Required" : "Optional"}
          </Badge>
        )
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl shadow-2xl border-0 animate-in fade-in slide-in-from-bottom-4 duration-300 max-h-[90vh] overflow-y-auto">
        <CardHeader className="border-b bg-gradient-to-r from-blue-50 to-indigo-50">
          <CardTitle className="flex items-center space-x-2 text-xl">
            <Key className="h-6 w-6 text-blue-600" />
            <span>API CONFIGURATION</span>
          </CardTitle>
          <p className="text-gray-600 mt-2">
            Connect your services to create powerful workflows
          </p>
        </CardHeader>

        <CardContent className="space-y-6 py-6">
          {/* Service Connections */}
          <div>
            <div className="flex items-center space-x-2 mb-4">
              <Key className="h-5 w-5 text-gray-600" />
              <span className="font-semibold text-gray-900">Service Connections</span>
            </div>

            <Card className="border-2 border-dashed border-gray-200">
              <CardContent className="p-6 space-y-6">
                {services.map((service) => {
                  const ServiceIcon = service.icon
                  const isWebhook = service.type === "webhook"
                  const fieldValue = service.value || ""
                  const showKey = showKeys[service.id]
                  
                  return (
                    <div key={service.id} className="space-y-3">
                      {/* Service Header */}
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-3">
                          <ServiceIcon className="h-5 w-5 text-blue-600" />
                          <span className="font-medium text-gray-900">{service.name}</span>
                        </div>
                        {getStatusBadge(service.status, service.required)}
                      </div>

                      {/* Service Description */}
                      <p className="text-sm text-gray-600 ml-8">{service.description}</p>

                      {/* Input Field */}
                      <div className="ml-8 space-y-2">
                        <div className="relative">
                          {service.type === "api-key" ? (
                            <div className="relative">
                              <Input
                                type={showKey ? "text" : "password"}
                                placeholder={service.placeholder}
                                value={fieldValue}
                                onChange={(e) => updateService(service.id, e.target.value)}
                                className="pr-20"
                              />
                              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center space-x-1">
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 w-6 p-0"
                                  onClick={() => toggleKeyVisibility(service.id)}
                                >
                                  {showKey ? (
                                    <EyeOff className="h-3 w-3" />
                                  ) : (
                                    <Eye className="h-3 w-3" />
                                  )}
                                </Button>
                              </div>
                            </div>
                          ) : (
                            <div className="relative">
                              <Input
                                value={fieldValue}
                                readOnly={isWebhook}
                                className={isWebhook ? "bg-gray-50 text-gray-700" : ""}
                                placeholder={service.placeholder}
                              />
                              {isWebhook && (
                                <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center space-x-1">
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-6 w-6 p-0"
                                    onClick={() => copyToClipboard(fieldValue, service.id)}
                                  >
                                    {copiedFields[service.id] ? (
                                      <Check className="h-3 w-3 text-green-600" />
                                    ) : (
                                      <Copy className="h-3 w-3" />
                                    )}
                                  </Button>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-6 w-6 p-0"
                                    onClick={regenerateWebhook}
                                  >
                                    <RefreshCw className="h-3 w-3" />
                                  </Button>
                                </div>
                              )}
                            </div>
                          )}
                        </div>

                        {/* Help Links */}
                        <div className="flex items-center space-x-4">
                          {service.helpUrl && (
                            <Button
                              variant="link"
                              size="sm"
                              className="h-auto p-0 text-xs"
                              asChild
                            >
                              <a href={service.helpUrl} target="_blank" rel="noopener noreferrer">
                                <ExternalLink className="h-3 w-3 mr-1" />
                                {service.type === "webhook" ? "Copy" : "Get API Key"}
                              </a>
                            </Button>
                          )}
                          <Button
                            variant="link"
                            size="sm"
                            className="h-auto p-0 text-xs text-muted-foreground"
                          >
                            <Info className="h-3 w-3 mr-1" />
                            {service.type === "webhook" ? "Regenerate" : "Why needed?"}
                          </Button>
                        </div>
                      </div>

                      {/* Separator */}
                      {service.id !== services[services.length - 1].id && (
                        <div className="border-t border-gray-100 my-4" />
                      )}
                    </div>
                  )
                })}
              </CardContent>
            </Card>
          </div>

          {/* Security Notice */}
          <div className="bg-green-50 border border-green-200 rounded-lg p-4">
            <div className="flex items-center space-x-2">
              <Lock className="h-4 w-4 text-green-600" />
              <span className="text-sm font-medium text-green-800">
                All keys encrypted and stored securely
              </span>
            </div>
            <p className="text-xs text-green-700 mt-1">
              Your API keys are encrypted using AES-256 encryption and stored in secure vaults
            </p>
          </div>

          {/* Action Buttons */}
          <div className="flex justify-between pt-4 border-t">
            <Button
              variant="outline"
              onClick={onSkip}
              disabled={isLoading}
            >
              Skip
            </Button>
            
            <div className="space-x-3">
              <Button
                variant="outline"
                onClick={onCancel}
                disabled={isLoading}
              >
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={isLoading}
                className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700"
              >
                {isLoading ? (
                  <div className="flex items-center space-x-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Testing...</span>
                  </div>
                ) : (
                  "Save & Test"
                )}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
