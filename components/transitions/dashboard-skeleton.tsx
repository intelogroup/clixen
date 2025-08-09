"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Loader2 } from "lucide-react"

interface DashboardSkeletonProps {
  showHeader?: boolean
}

export function DashboardSkeleton({ showHeader = true }: DashboardSkeletonProps) {
  const skeletonRows = Array.from({ length: 3 }, (_, i) => i)

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      {showHeader && (
        <div className="border-b bg-white px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <div className="h-6 bg-gray-200 rounded animate-pulse w-32"></div>
              <div className="h-6 bg-gray-200 rounded animate-pulse w-16"></div>
              <div className="h-6 bg-gray-200 rounded animate-pulse w-20"></div>
              <div className="h-6 bg-gray-200 rounded animate-pulse w-16"></div>
            </div>
            <div className="flex items-center space-x-4">
              <div className="h-6 bg-gray-200 rounded animate-pulse w-6"></div>
              <div className="h-6 bg-gray-200 rounded animate-pulse w-32"></div>
            </div>
          </div>
        </div>
      )}

      {/* Main Content */}
      <div className="p-6 space-y-6">
        {/* Title and Action */}
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <div className="h-6 w-6 bg-gray-200 rounded animate-pulse"></div>
            <div className="h-7 bg-gray-200 rounded animate-pulse w-32"></div>
          </div>
          <div className="h-9 bg-gray-200 rounded animate-pulse w-32"></div>
        </div>

        {/* Loading Skeleton */}
        <Card className="border-2 border-dashed border-gray-200">
          <CardHeader>
            <CardTitle className="text-base font-medium text-gray-600">
              LOADING SKELETON
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {skeletonRows.map((index) => (
              <div key={index} className="space-y-3">
                {/* Workflow Row */}
                <div className="flex items-center justify-between p-4 border border-gray-200 rounded-lg">
                  <div className="flex items-center space-x-4 flex-1">
                    <div className="h-4 bg-gray-200 rounded animate-pulse w-48"></div>
                    <div className="h-4 bg-gray-200 rounded animate-pulse w-20"></div>
                    <div className="h-4 bg-gray-200 rounded animate-pulse w-32"></div>
                    <div className="h-6 bg-gray-200 rounded animate-pulse w-16"></div>
                    <div className="h-4 bg-gray-200 rounded animate-pulse w-12"></div>
                  </div>
                </div>
                
                {/* Description Row */}
                <div className="ml-4">
                  <div className="h-3 bg-gray-200 rounded animate-pulse w-full max-w-md"></div>
                </div>

                {index < skeletonRows.length - 1 && (
                  <div className="border-t border-gray-100 my-4"></div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>

        {/* Loading Message */}
        <div className="text-center py-6 space-y-3">
          <div className="flex items-center justify-center space-x-2">
            <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
            <span className="text-gray-600 font-medium">Loading your workflows...</span>
          </div>
          <p className="text-sm text-muted-foreground">
            Please wait while we fetch your data
          </p>
          
          {/* Animated Loading Dots */}
          <div className="flex justify-center space-x-1 mt-4">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="w-2 h-2 bg-blue-400 rounded-full animate-pulse"
                style={{
                  animationDelay: `${i * 0.15}s`,
                  animationDuration: '1s'
                }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
