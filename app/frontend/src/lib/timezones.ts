const FALLBACK_TIMEZONES = ['UTC']

export function getTimezones(): string[] {
  if (typeof Intl.supportedValuesOf === 'function') {
    return Intl.supportedValuesOf('timeZone')
  }
  return FALLBACK_TIMEZONES
}
