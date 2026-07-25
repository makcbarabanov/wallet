/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
        display: ['"Fraunces"', 'Georgia', 'serif'],
      },
      colors: {
        ink: {
          50: '#F7F5F2',
          100: '#EDE9E3',
          200: '#D9D2C8',
          300: '#B8AFA3',
          400: '#8A8175',
          500: '#5C554C',
          600: '#3F3A34',
          700: '#2A2622',
          800: '#1A1816',
          900: '#0F0E0D',
        },
        pine: {
          400: '#3D8B6E',
          500: '#2F6B54',
          600: '#245443',
          700: '#1A3D31',
        },
        clay: {
          400: '#C4785A',
          500: '#A85D42',
          600: '#8A4A35',
        },
      },
      boxShadow: {
        soft: '0 1px 2px rgba(15,14,13,0.04), 0 8px 24px rgba(15,14,13,0.06)',
      },
    },
  },
  plugins: [],
}
