"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ModalManager, ModalType } from "@/components/modals/modal-manager"
import { ArrowLeft, Shield, Key, Settings, CheckCircle } from "lucide-react"
import { useRouter } from "next/navigation"

export default function ModalsDemo() {
  const router = useRouter()
  const [currentModal, setCurrentModal] = useState<ModalType>(null)
  const [selectedService, setSelectedService] = useState("Google Drive")
  const [results, setResults] = useState<Array<{ type: string, result: any }>>([])

  const oauthServices = [
    { name: "Google Drive", icon: "🗂️", description: "File storage and backup automation" },
    { name: "Gmail", icon: "📧", description: "Email automation and notifications" },
    { name: "Slack", icon: "💬", description: "Team communication and alerts" },
    { name: "Twitter", icon: "🐦", description: "Social media automation" }
  ]

  const handleModalComplete = (result?: any) => {
    console.log("Modal completed:", result)
    if (result) {
      setResults(prev => [...prev, { 
        type: currentModal || "unknown", 
        result,
        timestamp: new Date().toISOString()
      }])
    }
    setCurrentModal(null)
  }

  const handleModalCancel = () => {
    console.log("Modal cancelled")
    setCurrentModal(null)
  }

  const openOAuthModal = (service: string) => {
    setSelectedService(service)
    setCurrentModal("oauth-permission")
  }

  return (
    <ModalManager
      type={currentModal}
      service={selectedService}
      onComplete={handleModalComplete}
      onCancel={handleModalCancel}
    >
      <div className="min-h-screen bg-gradient-to-br from-blue-50 via-indigo-50 to-purple-50 p-6">
        <div className="max-w-6xl mx-auto space-y-8">
          {/* Header */}
          <div className="flex items-center space-x-4">
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.back()}
              className="flex items-center space-x-2"
            >
              <ArrowLeft className="h-4 w-4" />
              <span>Back</span>
            </Button>
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Modal Popups - Access Grants & Confirmations</h1>
              <p className="text-gray-600 mt-1">Professional modal components for OAuth permissions and API key setup</p>
            </div>
          </div>

          {/* Current Modal Status */}
          {currentModal && (
            <Card className="border-2 border-blue-200 bg-blue-50">
              <CardContent className="p-4">
                <div className="flex items-center space-x-3">
                  <div className="w-2 h-2 bg-blue-600 rounded-full animate-pulse" />
                  <span className="font-medium text-blue-800">
                    Active Modal: {currentModal === "oauth-permission" ? "OAuth Permission" : "API Setup"}
                    {currentModal === "oauth-permission" && ` (${selectedService})`}
                  </span>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            {/* OAuth Permission Modals */}
            <Card className="h-fit">
              <CardHeader>
                <CardTitle className="flex items-center space-x-3">
                  <Shield className="h-6 w-6 text-blue-600" />
                  <span>OAuth Access Permission</span>
                </CardTitle>
                <p className="text-gray-600 text-sm">
                  Request permission to access user services with clear permission lists
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  {oauthServices.map((service) => (
                    <Button
                      key={service.name}
                      variant="outline"
                      className="h-auto p-4 flex flex-col items-center space-y-2 hover:border-blue-300 hover:bg-blue-50"
                      onClick={() => openOAuthModal(service.name)}
                      disabled={!!currentModal}
                    >
                      <div className="text-2xl">{service.icon}</div>
                      <div className="text-center">
                        <div className="text-sm font-medium">{service.name}</div>
                        <div className="text-xs text-muted-foreground">
                          {service.description}
                        </div>
                      </div>
                    </Button>
                  ))}
                </div>
                
                <div className="pt-3 border-t">
                  <h4 className="font-medium text-sm mb-2">Features:</h4>
                  <ul className="text-xs text-gray-600 space-y-1">
                    <li>• Clear permission breakdown</li>
                    <li>• Security messaging</li>
                    <li>• Loading states and animations</li>
                    <li>• Responsive design</li>
                  </ul>
                </div>
              </CardContent>
            </Card>

            {/* API Setup Modal */}
            <Card className="h-fit">
              <CardHeader>
                <CardTitle className="flex items-center space-x-3">
                  <Key className="h-6 w-6 text-purple-600" />
                  <span>API Key Configuration</span>
                </CardTitle>
                <p className="text-gray-600 text-sm">
                  Comprehensive API key setup with service connections and security
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                <Button
                  onClick={() => setCurrentModal("api-setup")}
                  disabled={!!currentModal}
                  className="w-full h-20 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700"
                >
                  <div className="text-center">
                    <Settings className="h-8 w-8 mx-auto mb-2" />
                    <div className="text-lg font-medium">Open API Setup</div>
                    <div className="text-sm opacity-90">Configure service connections</div>
                  </div>
                </Button>

                <div className="pt-3 border-t">
                  <h4 className="font-medium text-sm mb-2">Features:</h4>
                  <ul className="text-xs text-gray-600 space-y-1">
                    <li>• Multiple API service support</li>
                    <li>• Password visibility toggle</li>
                    <li>• Auto-generated webhook URLs</li>
                    <li>• Help links and documentation</li>
                    <li>• Security encryption messaging</li>
                    <li>• Copy to clipboard functionality</li>
                  </ul>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Results Log */}
          {results.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center space-x-3">
                  <CheckCircle className="h-5 w-5 text-green-600" />
                  <span>Modal Results Log</span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3 max-h-64 overflow-y-auto">
                  {results.map((entry, index) => (
                    <div key={index} className="bg-gray-50 rounded-lg p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-medium text-sm capitalize">
                          {entry.type.replace("-", " ")}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {new Date(entry.timestamp).toLocaleTimeString()}
                        </span>
                      </div>
                      <pre className="text-xs bg-white rounded p-2 overflow-x-auto">
                        {JSON.stringify(entry.result, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Implementation Guide */}
          <Card className="border-2 border-gray-200">
            <CardHeader>
              <CardTitle className="text-lg">Implementation Guide</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-3">
                  <h4 className="font-semibold text-gray-900">🔐 OAuth Permission Modal</h4>
                  <div className="bg-gray-900 text-gray-100 p-4 rounded-lg text-sm font-mono overflow-x-auto">
                    <div className="space-y-1">
                      <div className="text-green-400">// Import and use OAuth modal</div>
                      <div>import {"{ ModalManager }"} from "@/components/modals/modal-manager"</div>
                      <div className="mt-2">{"<ModalManager"}</div>
                      <div>{"  type=\"oauth-permission\""}</div>
                      <div>{"  service=\"Google Drive\""}</div>
                      <div>{"  onComplete={(result) => console.log(result)}"}</div>
                      <div>{"/>"}</div>
                    </div>
                  </div>
                </div>
                
                <div className="space-y-3">
                  <h4 className="font-semibold text-gray-900">🔑 API Setup Modal</h4>
                  <div className="bg-gray-900 text-gray-100 p-4 rounded-lg text-sm font-mono overflow-x-auto">
                    <div className="space-y-1">
                      <div className="text-green-400">// Import and use API setup</div>
                      <div>import {"{ ModalManager }"} from "@/components/modals/modal-manager"</div>
                      <div className="mt-2">{"<ModalManager"}</div>
                      <div>{"  type=\"api-setup\""}</div>
                      <div>{"  onComplete={(services) => save(services)}"}</div>
                      <div>{"/>"}</div>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </ModalManager>
  )
}
