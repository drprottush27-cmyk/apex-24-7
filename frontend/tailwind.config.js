/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0A0D12',
        surface: {
          DEFAULT: '#12161D',
          2: '#181D26',
          3: '#1F2530',
        },
        border: {
          DEFAULT: '#232A35',
          subtle: '#19202A',
          focus: '#3A4659',
        },
        ink: {
          DEFAULT: '#E7E4DD',
          dim: '#8A93A0',
          muted: '#5B6472',
        },
        accent: {
          DEFAULT: '#F0A83C',
          dim: '#8A6528',
        },
        profit: '#35C48C',
        loss: '#E5484D',
        info: '#5B9BF2',
        warning: '#E5A835',
      },
      fontFamily: {
        ui: ['Space Grotesk', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        sm: '6px',
        md: '10px',
        lg: '14px',
        xl: '18px',
      },
      boxShadow: {
        subtle: '0 4px 24px rgba(0, 0, 0, 0.55)',
        glow: '0 0 14px rgba(53, 196, 140, 0.22)',
      },
      screens: {
        xs: '480px',
        sm: '640px',
        md: '768px',
        lg: '1024px',
        xl: '1280px',
      },
    },
  },
  plugins: [],
}