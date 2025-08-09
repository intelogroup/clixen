"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Shield, Check, X, Lock } from "lucide-react"

interface PermissionDialogProps {
  service: string
  permissions: Array<{
    action: string
    allowed: boolean
    description?: string
  }>
  onAllow?: () => void
  onCancel?: () => void
  isOpen?: boolean
}

export function PermissionDialog({
  service = "Google Drive",
  permissions = [
    { action: "Read your files", allowed: true },
    { action: "Create folders", allowed: true },
    { action: "Upload backups", allowed: true },
    { action: "Delete files (never)", allowed: false }
  ],
  onAllow,
  onCancel,
  isOpen = true
}: PermissionDialogProps) {
  const [isProcessing, setIsProcessing] = useState(false)

  const handleAllow = async () => {
    setIsProcessing(true)
    // Simulate permission processing
    setTimeout(() => {
      onAllow?.()
      setIsProcessing(false)
    }, 1500)
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md shadow-2xl border-0">
        <CardHeader className="text-center pb-4">
          <CardTitle className="flex items-center justify-center space-x-2 text-lg">
            <Shield className="h-5 w-5 text-blue-600" />
            <span>PERMISSION REQUEST</span>
          </CardTitle>
        </CardHeader>

        <CardContent className="text-center space-y-6">
          {/* Service Info */}
          <div className="space-y-2">
            <div className="text-xl font-semibold text-gray-900">
              Grant Access to
            </div>
            <div className="text-2xl font-bold text-blue-600">
              {service}
            </div>
          </div>

          {/* Bot Info */}
          <div className="space-y-2">
            <div className="flex items-center justify-center space-x-2">
              <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
              <span className="font-medium">Clixen AI needs</span>
            </div>
            <div className="text-sm text-muted-foreground">permission to:</div>
          </div>

          {/* Permissions List */}
          <div className="space-y-3 text-left">
            {permissions.map((permission, index) => (
              <div key={index} className="flex items-center space-x-3">
                {permission.allowed ? (
                  <Check className="h-4 w-4 text-green-600 flex-shrink-0" />
                ) : (
                  <X className="h-4 w-4 text-red-600 flex-shrink-0" />
                )}
                <span className={`text-sm ${
                  permission.allowed ? 'text-gray-700' : 'text-red-600'
                }`}>
                  {permission.action}
                </span>
              </div>
            ))}
          </div>

          {/* Security Message */}
          <div className="bg-green-50 border border-green-200 rounded-lg p-4">
            <div className="flex items-center justify-center space-x-2">
              <Lock className="h-4 w-4 text-green-600" />
              <span className="text-sm font-medium text-green-800">
                Your data stays
              </span>
            </div>
            <div className="text-sm font-bold text-green-800 mt-1">
              secure and private
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex space-x-3">
            <Button
              variant="outline"
              className="flex-1"
              onClick={onCancel}
              disabled={isProcessing}
            >
              Cancel
            </Button>
            <Button
              className="flex-1 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700"
              onClick={handleAllow}
              disabled={isProcessing}
            >
              {isProcessing ? (
                <div className="flex items-center space-x-2">
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                  <span>Connecting...</span>
                </div>
              ) : (
                <div className="flex items-center space-x-2">
                  <span>Allow</span>
                  <span className="text-yellow-300">✨</span>
                </div>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
