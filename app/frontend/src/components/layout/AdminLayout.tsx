import type { ReactNode } from 'react'
import { Layout } from './Layout'
import { AdminSidebar } from '@/components/sidebar/AdminSidebar'

interface AdminLayoutProps {
  title: string
  children: ReactNode
}

/**
 * Admin page wrapper combining Layout, AdminSidebar, and a page title header.
 */
export function AdminLayout({ title, children }: AdminLayoutProps) {
  return (
    <Layout
      sidebar={<AdminSidebar />}
      header={
        <div className="mx-auto w-full max-w-5xl px-4 py-3">
          <h1 className="text-lg font-semibold">{title}</h1>
        </div>
      }
    >
      <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col">
        {children}
      </div>
    </Layout>
  )
}
