"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Bot, ArrowLeft, Mail, CheckCircle, ArrowRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import Link from "next/link"

export default function ForgotPasswordPage() {
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [isSubmitted, setIsSubmitted] = useState(false)
  const [error, setError] = useState("")

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError("")
    
    // Simulate API call
    await new Promise(resolve => setTimeout(resolve, 2000))
    
    if (email) {
      setIsSubmitted(true)
    } else {
      setError("Please enter a valid email address.")
    }
    
    setIsLoading(false)
  }

  if (isSubmitted) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="text-center mb-8">
            <div className="flex items-center justify-center space-x-2 mb-4">
              <Bot className="h-8 w-8 text-blue-600" />
              <span className="text-2xl font-bold text-gray-900">Clixen</span>
            </div>
          </div>

          <Card className="border-2 shadow-lg">
            <CardContent className="p-8 text-center">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <CheckCircle className="h-8 w-8 text-green-600" />
              </div>
              
              <h1 className="text-2xl font-bold text-gray-900 mb-2">Check your email</h1>
              <p className="text-gray-600 mb-6">
                We've sent a password reset link to{" "}
                <span className="font-medium text-gray-900">{email}</span>
              </p>

              <div className="space-y-4">
                <Button
                  className="w-full h-11 bg-blue-600 hover:bg-blue-700"
                  onClick={() => window.open('mailto:', '_blank')}
                >
                  <Mail className="h-4 w-4 mr-2" />
                  Open Email App
                </Button>

                <div className="text-sm text-gray-500">
                  <p className="mb-2">Didn't receive the email? Check your spam folder or</p>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setIsSubmitted(false)}
                    className="text-blue-600 hover:text-blue-700 p-0 h-auto"
                  >
                    try a different email address
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="mt-6 text-center">
            <Link href="/auth/signin" className="text-sm text-blue-600 hover:underline flex items-center justify-center">
              <ArrowLeft className="h-4 w-4 mr-1" />
              Back to sign in
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push('/auth/signin')}
            className="absolute top-4 left-4 md:top-8 md:left-8"
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Sign In
          </Button>
          
          <div className="flex items-center justify-center space-x-2 mb-4">
            <Bot className="h-8 w-8 text-blue-600" />
            <span className="text-2xl font-bold text-gray-900">Clixen</span>
          </div>
          
          <h1 className="text-2xl font-bold text-gray-900 mb-2">Forgot your password?</h1>
          <p className="text-gray-600">No worries! Enter your email and we'll send you reset instructions.</p>
        </div>

        <Card className="border-2 shadow-lg">
          <CardHeader>
            <CardTitle className="text-center text-lg font-semibold">Reset Password</CardTitle>
          </CardHeader>
          
          <CardContent>
            {error && (
              <Alert className="mb-4 border-red-200 bg-red-50">
                <AlertDescription className="text-red-800">
                  {error}
                </AlertDescription>
              </Alert>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                  Email address
                </label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-11"
                  placeholder="john@company.com"
                />
              </div>

              <Button
                type="submit"
                className="w-full h-11 bg-blue-600 hover:bg-blue-700"
                disabled={isLoading}
              >
                {isLoading ? (
                  <div className="flex items-center">
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />
                    Sending reset link...
                  </div>
                ) : (
                  <>
                    Send reset link
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </>
                )}
              </Button>
            </form>

            <div className="mt-6 text-center text-sm">
              <span className="text-gray-600">Remember your password? </span>
              <Link href="/auth/signin" className="text-blue-600 hover:underline font-medium">
                Sign in
              </Link>
            </div>
          </CardContent>
        </Card>

        {/* Help Section */}
        <Card className="mt-6 border border-gray-200 bg-gray-50">
          <CardContent className="p-4">
            <h3 className="text-sm font-medium text-gray-900 mb-2">Need help?</h3>
            <p className="text-xs text-gray-600 mb-3">
              If you're still having trouble accessing your account, our support team is here to help.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="w-full"
              onClick={() => window.open('mailto:support@clixen.com', '_blank')}
            >
              <Mail className="h-3 w-3 mr-2" />
              Contact Support
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
