export function ErrorAlert({ error }: { error: string }) {
  if (!error) return null
  return (
    <div className="rounded-md bg-red-50 p-3 text-sm text-red-500 dark:bg-red-950">
      {error}
    </div>
  )
}
