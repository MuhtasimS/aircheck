// Thin, mockable navigation seam. Programmatic navigation after a command
// (create run, resolve decision) goes through here so it can be stubbed in
// tests without touching jsdom's unimplemented navigation.
export function navigateTo(url: string): void {
  if (typeof window !== 'undefined') {
    window.location.assign(url);
  }
}
