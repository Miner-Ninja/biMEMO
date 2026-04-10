export const ALLOWED_FILE_EXTENSIONS = [
  '.txt',
  '.md',
  '.pdf',
  '.csv',
  '.json',
  '.xml',
  '.html',
  '.py',
  '.js',
  '.ts',
  '.yaml',
  '.yml',
  '.log',
  '.sh',
  '.sql',
  '.toml',
  '.ini',
  '.cfg',
  '.conf',
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
] as const

export const FILE_INPUT_ACCEPT = ALLOWED_FILE_EXTENSIONS.join(',')

export function isAllowedUploadFilename(filename: string): boolean {
  const dotIndex = filename.lastIndexOf('.')
  if (dotIndex === -1) return false
  const ext = filename.slice(dotIndex).toLowerCase()
  return ALLOWED_FILE_EXTENSIONS.includes(ext as (typeof ALLOWED_FILE_EXTENSIONS)[number])
}
