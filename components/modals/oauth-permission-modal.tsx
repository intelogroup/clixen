"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Shield, Check, X, Lock, Sparkles, Loader2 } from "lucide-react"

interface Permission {
  action: string
  allowed: boolean
  description?: string
}

interface OAuthPermissionModalProps {
  isOpen: boolean
  service: string
  permissions: Permission[]
  onAllow: () => void
  onCancel: () => void
  isLoading?: boolean
}

export function OAuthPermissionModal({
  isOpen,
  service,
  permissions,
  onAllow,
  onCancel,
  isLoading = false
}: OAuthPermissionModalProps) {
  const [isProcessing, setIsProcessing] = useState(false)

  if (!isOpen) return null

  const handleAllow = async () => {
    setIsProcessing(true)
    // Simulate OAuth flow
    setTimeout(() => {
      onAllow()
      setIsProcessing(false)
    }, 2000)
  }

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md shadow-2xl border-0 animate-in fade-in slide-in-from-bottom-4 duration-300">
        <CardHeader className="text-center pb-4 border-b">
          <CardTitle className="flex items-center justify-center space-x-2 text-lg">
            <Shield className="h-5 w-5 text-blue-600" />
            <span>PERMISSION REQUEST</span>
          </CardTitle>
        </CardHeader>

        <CardContent className="text-center space-y-6 py-6">
          {/* Service Access Request */}
          <div className="space-y-3">
            <div className="flex items-center justify-center space-x-2">
              <Shield className="h-5 w-5 text-blue-600" />
              <span className="text-lg font-semibold text-gray-900">
                Grant Access to
              </span>
            </div>
            <div className="text-2xl font-bold text-blue-600">
              {service}
            </div>
          </div>

          {/* Bot Request */}
          <div className="space-y-2">
            <div className="flex items-center justify-center space-x-2">
              <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
              <span className="font-medium text-gray-800">Clixen AI needs</span>
            </div>
            <div className="text-sm text-muted-foreground">permission to:</div>
          </div>

          {/* Permissions List */}
          <div className="space-y-3 text-left bg-gray-50 rounded-lg p-4">
            {permissions.map((permission, index) => (
              <div key={index} className="flex items-center space-x-3">
                {permission.allowed ? (
                  <Check className="h-4 w-4 text-green-600 flex-shrink-0" />
                ) : (
                  <X className="h-4 w-4 text-red-600 flex-shrink-0" />
                )}
                <span className={`text-sm ${
                  permission.allowed ? 'text-gray-700' : 'text-red-600 font-medium'
                }`}>
                  {permission.action}
                </span>
                {!permission.allowed && (
                  <Badge variant="destructive" className="text-xs px-2 py-0">
                    Never
                  </Badge>
                )}
              </div>
            ))}
          </div>

          {/* Security Assurance */}
          <div className="bg-green-50 border border-green-200 rounded-lg p-4">
            <div className="flex items-center justify-center space-x-2 mb-2">
              <Lock className="h-4 w-4 text-green-600" />
              <span className="text-sm font-medium text-green-800">
                Your data stays
              </span>
            </div>
            <div className="text-sm font-bold text-green-800">
              secure and private
            </div>
            <div className="text-xs text-green-700 mt-1">
              We use industry-standard encryption and never share your data
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex space-x-3 pt-2">
            <Button
              variant="outline"
              className="flex-1"
              onClick={onCancel}
              disabled={isProcessing || isLoading}
            >
              Cancel
            </Button>
            <Button
              className="flex-1 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white"
              onClick={handleAllow}
              disabled={isProcessing || isLoading}
            >
              {isProcessing || isLoading ? (
                <div className="flex items-center space-x-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Connecting...</span>
                </div>
              ) : (
                <div className="flex items-center space-x-2">
                  <span>Allow</span>
                  <Sparkles className="w-4 h-4 text-yellow-300" />
                </div>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
