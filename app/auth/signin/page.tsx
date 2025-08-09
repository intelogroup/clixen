"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Bot, Eye, EyeOff, ArrowLeft, Github, Chrome, Mail } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Alert, AlertDescription } from "@/components/ui/alert"
// import { useAuthActions } from "@convex-dev/auth/react"
import Link from "next/link"

export default function SignInPage() {
  const router = useRouter()
  const { signIn } = useAuthActions()
  const [showPassword, setShowPassword] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState("")
  const [formData, setFormData] = useState({
    email: "",
    password: "",
    rememberMe: false
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError("")
    
    try {
      await signIn("password", {
        email: formData.email,
        password: formData.password,
        flow: "signIn",
      })
      
      // The auth will redirect automatically on success
    } catch (err: any) {
      setError(err.message || "Invalid email or password. Please try again.")
      setIsLoading(false)
    }
  }

  const handleSocialAuth = async (provider: "github" | "google") => {
    try {
      await signIn(provider)
    } catch (err: any) {
      setError(err.message || `Failed to sign in with ${provider}`)
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData(prev => ({
      ...prev,
      [e.target.name]: e.target.value
    }))
  }

  const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData(prev => ({
      ...prev,
      rememberMe: e.target.checked
    }))
  }

  const handleDemoAccount = () => {
    setFormData({
      email: "demo@clixen.com",
      password: "demo123",
      rememberMe: false
    })
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push('/')}
            className="absolute top-4 left-4 md:top-8 md:left-8"
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Home
          </Button>
          
          <div className="flex items-center justify-center space-x-2 mb-4">
            <Bot className="h-8 w-8 text-blue-600" />
            <span className="text-2xl font-bold text-gray-900">Clixen</span>
          </div>
          
          <h1 className="text-2xl font-bold text-gray-900 mb-2">Welcome back</h1>
          <p className="text-gray-600">Sign in to your automation workspace</p>
        </div>

        <Card className="border-2 shadow-lg">
          <CardHeader className="space-y-4">
            {/* Social Auth Buttons */}
            <div className="space-y-3">
              <Button
                variant="outline"
                className="w-full h-11 border-2"
                onClick={() => handleSocialAuth('google')}
                disabled={isLoading}
              >
                <Chrome className="h-4 w-4 mr-2" />
                Continue with Google
              </Button>
              <Button
                variant="outline"
                className="w-full h-11 border-2"
                onClick={() => handleSocialAuth('github')}
                disabled={isLoading}
              >
                <Github className="h-4 w-4 mr-2" />
                Continue with GitHub
              </Button>
            </div>

            <div className="relative">
              <Separator />
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="bg-white px-4 text-sm text-gray-500">or continue with email</span>
              </div>
            </div>
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
              {/* Email */}
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                  Email address
                </label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  required
                  value={formData.email}
                  onChange={handleInputChange}
                  className="h-11"
                  placeholder="john@company.com"
                  disabled={isLoading}
                />
              </div>

              {/* Password */}
              <div>
                <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <div className="relative">
                  <Input
                    id="password"
                    name="password"
                    type={showPassword ? "text" : "password"}
                    required
                    value={formData.password}
                    onChange={handleInputChange}
                    className="h-11 pr-10"
                    placeholder="Enter your password"
                    disabled={isLoading}
                  />
                  <button
                    type="button"
                    className="absolute inset-y-0 right-0 pr-3 flex items-center"
                    onClick={() => setShowPassword(!showPassword)}
                    disabled={isLoading}
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4 text-gray-400" />
                    ) : (
                      <Eye className="h-4 w-4 text-gray-400" />
                    )}
                  </button>
                </div>
              </div>

              {/* Remember Me & Forgot Password */}
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    id="rememberMe"
                    name="rememberMe"
                    checked={formData.rememberMe}
                    onChange={handleCheckboxChange}
                    className="rounded border-gray-300"
                    disabled={isLoading}
                  />
                  <label htmlFor="rememberMe" className="text-sm text-gray-600">
                    Remember me
                  </label>
                </div>
                <Link href="/auth/forgot-password" className="text-sm text-blue-600 hover:underline">
                  Forgot password?
                </Link>
              </div>

              {/* Submit Button */}
              <Button
                type="submit"
                className="w-full h-11 bg-blue-600 hover:bg-blue-700"
                disabled={isLoading}
              >
                {isLoading ? (
                  <div className="flex items-center">
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />
                    Signing in...
                  </div>
                ) : (
                  "Sign in"
                )}
              </Button>
            </form>

            {/* Sign Up Link */}
            <div className="mt-6 text-center text-sm">
              <span className="text-gray-600">Don't have an account? </span>
              <Link href="/auth/signup" className="text-blue-600 hover:underline font-medium">
                Sign up for free
              </Link>
            </div>
          </CardContent>
        </Card>

        {/* Demo Account */}
        <Card className="mt-6 border border-blue-200 bg-blue-50">
          <CardContent className="p-4">
            <div className="flex items-start space-x-3">
              <Mail className="h-5 w-5 text-blue-600 mt-0.5" />
              <div>
                <h3 className="text-sm font-medium text-blue-900 mb-1">Try Demo Account</h3>
                <p className="text-xs text-blue-700 mb-2">
                  Use demo@clixen.com with password "demo123" to explore all features
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-blue-300 text-blue-700 hover:bg-blue-100"
                  onClick={handleDemoAccount}
                  disabled={isLoading}
                >
                  Use Demo Account
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
