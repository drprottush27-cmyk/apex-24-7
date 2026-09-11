/**
 * Telegram Mini App adapter boundary.
 *
 * Safe by construction: never requires Telegram during development, never
 * crashes outside Telegram, and performs NO authentication. Client-side
 * identity is NOT trusted anywhere in the app.
 */

interface TelegramWebAppStub {
  ready?: () => void
  initData?: string
  initDataUnsafe?: Record<string, unknown>
  colorScheme?: 'light' | 'dark'
  expand?: () => void
  close?: () => void
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebAppStub
    }
  }
}

export interface TelegramPlatform {
  isTelegram: boolean
  webApp: TelegramWebAppStub | null
  /** Raw initData if present. Never treated as proof of identity. */
  initData: string | null
}

/**
 * Safely detect the Telegram WebApp bridge. All access is guarded so the app
 * works identically in a normal browser.
 */
export function detectTelegram(): TelegramPlatform {
  try {
    const webApp = window.Telegram?.WebApp ?? null
    if (!webApp) {
      return { isTelegram: false, webApp: null, initData: null }
    }
    // Accessing the raw string is safe (no parsing/signing here).
    const initData = typeof webApp.initData === 'string' ? webApp.initData : null
    return { isTelegram: true, webApp, initData }
  } catch {
    // Never crash a normal browser session because of probing.
    return { isTelegram: false, webApp: null, initData: null }
  }
}

/** Invoke Telegram-safe lifecycle hooks with no throw path. */
export function goTelegramReady(): void {
  try {
    const platform = detectTelegram()
    if (platform.isTelegram && platform.webApp?.ready) {
      platform.webApp.ready()
    }
  } catch {
    // ignored
  }
}

export const telegramPlatform: TelegramPlatform = detectTelegram()